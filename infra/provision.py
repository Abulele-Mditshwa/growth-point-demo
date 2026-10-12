#!/usr/bin/env python3
"""
Provision the live AWS Bedrock Knowledge Base backed by Amazon S3 Vectors for the
Growthpoint Knowledge Assistant, and ingest the mock corpus.

Pure boto3 (no external IaC). The script is idempotent where practical: every
resource is "create or reuse". It:

  1. Creates/reuses an S3 source bucket and uploads each corpus document as
     documents/<id>.txt plus a Bedrock metadata sidecar documents/<id>.txt.metadata.json.
  2. Creates/reuses an Amazon S3 Vectors vector bucket and vector index
     (Titan V2: 1024 dims, cosine), with the KB-managed chunk fields marked
     non-filterable so our small custom metadata (incl. access_level) stays filterable.
  3. Creates/reuses an IAM service role for the Bedrock KB.
  4. Creates/reuses the Knowledge Base (VECTOR type, Titan V2) wired to the S3 Vectors index.
  5. Creates/reuses the S3 data source and runs an ingestion job to completion.
  6. Prints KNOWLEDGE_BASE_ID + the exact env vars and writes .env if absent.

Vector store is Amazon S3 Vectors (NOT OpenSearch). Default region is us-east-1
(S3 Vectors + Bedrock KB availability; af-south-1 is not reliable). Claude 3 Haiku
is the cheapest Claude tier for generation (set at app runtime via LLM_MODEL_ID).

Usage:
    python infra/provision.py [--dry-run] [--region us-east-1]
                              [--profile abulele-ntt-sandbox] [--prefix growthpoint-rag]

--help and --dry-run never make any AWS call.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List


# --------------------------------------------------------------------------
# Constants (decisions fixed by the task)
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "src" / "mock" / "documents" / "corpus.json"
ENV_PATH = ROOT / ".env"

EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
EMBEDDING_DISTANCE_METRIC = os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine")
# Cheapest ACTIVE Claude on Bedrock. Claude 3 Haiku reached end-of-life (Sept
# 2026); Haiku 4.5 is the current cheapest and is served via an inference profile.
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_PROFILE = os.getenv("AWS_PROFILE", "abulele-ntt-sandbox")
DEFAULT_PREFIX = "growthpoint-rag"

# S3 Vectors stores all custom metadata as filterable by default; only the KB's
# large managed chunk fields must be excluded so they don't blow the filterable
# metadata budget. access_level/title/category/doc_ref/section stay filterable.
NON_FILTERABLE_METADATA_KEYS = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]

# Only these small fields go into the sidecar; never the document content.
METADATA_FIELDS = ("access_level", "title", "category", "doc_ref", "section")

logger = logging.getLogger("provision")


# ==========================================================================
# Pure, AWS-free helpers (importable + unit-testable without AWS)
# ==========================================================================
def load_corpus(path) -> List[Dict[str, Any]]:
    """Read corpus.json and return the list of document dicts."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("documents", [])


def build_metadata_sidecar(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Bedrock metadata sidecar body (small; no content).

    Simplified metadataAttributes form: values are stored for filtering but not
    embedded, which is what we want for access control.
    """
    return {
        "metadataAttributes": {
            "access_level": str(doc.get("access_level", "public")),
            "title": str(doc.get("title", "")),
            "category": str(doc.get("category", "")),
            "doc_ref": str(doc.get("doc_ref", "")),
            "section": str(doc.get("section", "")),
        }
    }


def document_object_key(doc: Dict[str, Any]) -> str:
    """S3 key for a document body, e.g. documents/LSE-001.txt."""
    return f"documents/{doc['id']}.txt"


def metadata_object_key(doc: Dict[str, Any]) -> str:
    """S3 key for the sidecar, e.g. documents/LSE-001.txt.metadata.json."""
    return document_object_key(doc) + ".metadata.json"


def resource_names(prefix: str = DEFAULT_PREFIX, account_id: str = "") -> Dict[str, str]:
    """Deterministic resource names. Bucket names include the account id (if known)
    for global-uniqueness; names are lowercased and DNS-safe."""
    suffix = f"-{account_id}" if account_id else ""
    return {
        "source_bucket": f"{prefix}-source{suffix}",
        "vector_bucket": f"{prefix}-vectors{suffix}",
        "vector_index": f"{prefix}-index",
        "knowledge_base": f"{prefix}-kb",
        "data_source": f"{prefix}-s3-source",
        "role_name": f"{prefix}-kb-role",
    }


def bedrock_trust_policy() -> Dict[str, Any]:
    """IAM trust policy allowing the Bedrock service to assume the KB role."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def kb_role_permissions_policy(
    source_bucket: str,
    vector_bucket_arn: str,
    index_arn: str,
    region: str,
    account_id: str,
) -> Dict[str, Any]:
    """Inline permissions policy for the Bedrock KB service role."""
    embed_model_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL_ID}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "SourceBucketRead",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{source_bucket}",
                    f"arn:aws:s3:::{source_bucket}/*",
                ],
            },
            {
                "Sid": "S3VectorsAccess",
                "Effect": "Allow",
                "Action": [
                    "s3vectors:GetVectorBucket",
                    "s3vectors:GetIndex",
                    "s3vectors:ListIndexes",
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:ListVectors",
                ],
                "Resource": [vector_bucket_arn, index_arn],
            },
            {
                "Sid": "InvokeEmbeddingModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [embed_model_arn],
            },
            {
                "Sid": "KmsForBedrock",
                "Effect": "Allow",
                "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"aws:PrincipalAccount": account_id}
                },
            },
        ],
    }


def plan_steps(names: Dict[str, str], region: str, profile: str) -> List[str]:
    """Human-readable plan for --dry-run (no AWS calls)."""
    return [
        f"Profile: {profile}   Region: {region}",
        f"Embeddings: {EMBEDDING_MODEL_ID} ({EMBEDDING_DIMENSION}-d, {EMBEDDING_DISTANCE_METRIC})",
        f"Generation model (set at app runtime): {LLM_MODEL_ID} (cheapest Claude)",
        f"1. S3 source bucket:   {names['source_bucket']}  (upload documents/*.txt + *.metadata.json)",
        f"2. S3 Vectors bucket:  {names['vector_bucket']}",
        f"   S3 Vectors index:   {names['vector_index']}  "
        f"(dim={EMBEDDING_DIMENSION}, metric={EMBEDDING_DISTANCE_METRIC}, "
        f"non-filterable={NON_FILTERABLE_METADATA_KEYS})",
        f"3. IAM KB role:        {names['role_name']}  (trust bedrock.amazonaws.com)",
        f"4. Knowledge Base:     {names['knowledge_base']}  (VECTOR, Titan V2, S3_VECTORS storage)",
        f"5. Data source:        {names['data_source']}  (S3 -> {names['source_bucket']})",
        "6. Start ingestion job and poll to COMPLETE.",
        "7. Print KNOWLEDGE_BASE_ID + env vars; write .env if it does not exist.",
    ]


def render_env_block(region: str, profile: str, kb_id: str) -> str:
    """The exact env block to print / write once the KB exists."""
    return (
        "USE_MOCK=false\n"
        f"AWS_REGION={region}\n"
        f"AWS_PROFILE={profile}\n"
        f"KNOWLEDGE_BASE_ID={kb_id}\n"
        f"LLM_MODEL_ID={LLM_MODEL_ID}\n"
    )


# ==========================================================================
# Argument parsing (runs BEFORE any boto3 Session/client is created)
# ==========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="provision.py",
        description=(
            "Provision a Bedrock Knowledge Base on Amazon S3 Vectors for the "
            "Growthpoint Knowledge Assistant and ingest the mock corpus."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help="AWS region (S3 Vectors + Bedrock KB; us-east-1 recommended).",
    )
    p.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help="AWS named profile to use for all AWS calls.",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help="Name prefix for all created resources.",
    )
    p.add_argument(
        "--dry-run",
        "--plan",
        dest="dry_run",
        action="store_true",
        help="Print the intended steps and resource names without any AWS call.",
    )
    return p


# ==========================================================================
# Live execution (only reached when not --dry-run; constructs clients here)
# ==========================================================================
def _bucket_exists(s3, bucket: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False


def ensure_source_bucket(s3, bucket: str, region: str) -> str:
    if _bucket_exists(s3, bucket):
        logger.info("Reusing S3 source bucket %s", bucket)
    else:
        logger.info("Creating S3 source bucket %s", bucket)
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
    return f"arn:aws:s3:::{bucket}"


def upload_corpus(s3, bucket: str, docs: List[Dict[str, Any]]) -> None:
    for doc in docs:
        body_key = document_object_key(doc)
        meta_key = metadata_object_key(doc)
        s3.put_object(
            Bucket=bucket,
            Key=body_key,
            Body=doc["content"].encode("utf-8"),
            ContentType="text/plain",
        )
        s3.put_object(
            Bucket=bucket,
            Key=meta_key,
            Body=json.dumps(build_metadata_sidecar(doc)).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Uploaded %s (+ sidecar)", body_key)


def ensure_vector_bucket(s3vectors, name: str) -> str:
    from botocore.exceptions import ClientError

    try:
        s3vectors.create_vector_bucket(vectorBucketName=name)
        logger.info("Created S3 Vectors bucket %s", name)
    except ClientError as e:
        if e.response["Error"].get("Code") in ("ConflictException", "BucketAlreadyExists"):
            logger.info("Reusing S3 Vectors bucket %s", name)
        else:
            raise
    return s3vectors.get_vector_bucket(vectorBucketName=name)["vectorBucket"]["vectorBucketArn"]


def ensure_vector_index(s3vectors, bucket: str, index: str) -> str:
    from botocore.exceptions import ClientError

    try:
        s3vectors.create_index(
            vectorBucketName=bucket,
            indexName=index,
            dataType="float32",
            dimension=EMBEDDING_DIMENSION,
            distanceMetric=EMBEDDING_DISTANCE_METRIC,
            metadataConfiguration={
                "nonFilterableMetadataKeys": NON_FILTERABLE_METADATA_KEYS
            },
        )
        logger.info("Created S3 Vectors index %s", index)
    except ClientError as e:
        if e.response["Error"].get("Code") in ("ConflictException",):
            logger.info("Reusing S3 Vectors index %s", index)
        else:
            raise
    return s3vectors.get_index(vectorBucketName=bucket, indexName=index)["index"]["indexArn"]


def ensure_kb_role(
    iam, name: str, source_bucket: str, vector_bucket_arn: str,
    index_arn: str, region: str, account_id: str,
) -> str:
    from botocore.exceptions import ClientError

    trust = json.dumps(bedrock_trust_policy())
    try:
        role = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=trust,
            Description="Bedrock Knowledge Base service role (Growthpoint RAG PoC).",
        )
        role_arn = role["Role"]["Arn"]
        logger.info("Created IAM role %s", name)
    except ClientError as e:
        if e.response["Error"].get("Code") == "EntityAlreadyExists":
            role_arn = iam.get_role(RoleName=name)["Role"]["Arn"]
            logger.info("Reusing IAM role %s", name)
        else:
            raise
    iam.put_role_policy(
        RoleName=name,
        PolicyName=f"{name}-permissions",
        PolicyDocument=json.dumps(
            kb_role_permissions_policy(
                source_bucket, vector_bucket_arn, index_arn, region, account_id
            )
        ),
    )
    logger.info("Attached inline permissions policy to %s", name)
    return role_arn


def ensure_knowledge_base(
    bedrock_agent, name: str, role_arn: str,
    vector_bucket_arn: str, index_arn: str, region: str,
) -> str:
    from botocore.exceptions import ClientError

    # Reuse by name if it already exists.
    for kb in bedrock_agent.list_knowledge_bases().get("knowledgeBaseSummaries", []):
        if kb["name"] == name:
            logger.info("Reusing Knowledge Base %s (%s)", name, kb["knowledgeBaseId"])
            return kb["knowledgeBaseId"]

    embed_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL_ID}"
    kb_config = {
        "type": "VECTOR",
        "vectorKnowledgeBaseConfiguration": {
            "embeddingModelArn": embed_arn,
            "embeddingModelConfiguration": {
                "bedrockEmbeddingModelConfiguration": {
                    "dimensions": EMBEDDING_DIMENSION,
                    "embeddingDataType": "FLOAT32",
                }
            },
        },
    }
    storage_config = {
        "type": "S3_VECTORS",
        "s3VectorsConfiguration": {
            "vectorBucketArn": vector_bucket_arn,
            "indexArn": index_arn,
        },
    }

    # IAM role propagation can lag; retry the transient "cannot assume role" error.
    last_err = None
    for attempt in range(5):
        try:
            resp = bedrock_agent.create_knowledge_base(
                name=name,
                roleArn=role_arn,
                knowledgeBaseConfiguration=kb_config,
                storageConfiguration=storage_config,
            )
            kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
            logger.info("Created Knowledge Base %s (%s)", name, kb_id)
            return kb_id
        except ClientError as e:
            last_err = e
            msg = str(e)
            if "assume" in msg.lower() or e.response["Error"].get("Code") == "ValidationException":
                logger.warning("KB create attempt %d failed (role propagation?); retrying: %s",
                               attempt + 1, msg)
                time.sleep(10)
                continue
            raise
    raise last_err


def ensure_data_source(bedrock_agent, kb_id: str, name: str, source_bucket_arn: str) -> str:
    for ds in bedrock_agent.list_data_sources(knowledgeBaseId=kb_id).get(
        "dataSourceSummaries", []
    ):
        if ds["name"] == name:
            logger.info("Reusing data source %s (%s)", name, ds["dataSourceId"])
            return ds["dataSourceId"]
    resp = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=name,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": source_bucket_arn,
                "inclusionPrefixes": ["documents/"],
            },
        },
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    logger.info("Created data source %s (%s)", name, ds_id)
    return ds_id


def run_ingestion(bedrock_agent, kb_id: str, ds_id: str, timeout_s: int = 900) -> str:
    job = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = job["ingestionJob"]["ingestionJobId"]
    logger.info("Started ingestion job %s", job_id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]["status"]
        logger.info("Ingestion status: %s", status)
        if status in ("COMPLETE", "FAILED"):
            return status
        time.sleep(10)
    return "TIMEOUT"


def run_live(args) -> int:
    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    logger.info("Resolved profile=%s region=%s", args.profile, args.region)
    sts = session.client("sts")
    s3 = session.client("s3")
    s3vectors = session.client("s3vectors")
    iam = session.client("iam")
    bedrock_agent = session.client("bedrock-agent")

    account_id = sts.get_caller_identity()["Account"]
    names = resource_names(args.prefix, account_id)

    docs = load_corpus(CORPUS_PATH)
    logger.info("Loaded %d corpus documents", len(docs))

    source_bucket_arn = ensure_source_bucket(s3, names["source_bucket"], args.region)
    upload_corpus(s3, names["source_bucket"], docs)

    vector_bucket_arn = ensure_vector_bucket(s3vectors, names["vector_bucket"])
    index_arn = ensure_vector_index(s3vectors, names["vector_bucket"], names["vector_index"])

    role_arn = ensure_kb_role(
        iam, names["role_name"], names["source_bucket"],
        vector_bucket_arn, index_arn, args.region, account_id,
    )

    kb_id = ensure_knowledge_base(
        bedrock_agent, names["knowledge_base"], role_arn,
        vector_bucket_arn, index_arn, args.region,
    )
    ds_id = ensure_data_source(
        bedrock_agent, kb_id, names["data_source"], source_bucket_arn
    )
    status = run_ingestion(bedrock_agent, kb_id, ds_id)
    if status != "COMPLETE":
        logger.error("Ingestion did not complete (status=%s). KB id: %s", status, kb_id)
        return 1

    env_block = render_env_block(args.region, args.profile, kb_id)
    print("\n" + "=" * 64)
    print(f"KNOWLEDGE_BASE_ID={kb_id}")
    print("Set these environment variables (or source the .env file):\n")
    print(env_block)
    print("=" * 64)

    if ENV_PATH.exists():
        logger.info(".env already exists at %s; not overwriting. Values printed above.", ENV_PATH)
    else:
        ENV_PATH.write_text(env_block, encoding="utf-8")
        logger.info("Wrote %s", ENV_PATH)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        # No AWS: account id is unknown, so show names without the account suffix.
        names = resource_names(args.prefix)
        print("DRY RUN — planned steps (no AWS calls, resource names omit account id):\n")
        for line in plan_steps(names, args.region, args.profile):
            print("  " + line)
        return 0

    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())

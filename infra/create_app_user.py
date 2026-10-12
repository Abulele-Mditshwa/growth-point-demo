#!/usr/bin/env python3
"""
Create a tightly-scoped IAM user + access key for hosting the Growthpoint
Knowledge Assistant on Streamlit Community Cloud (Option A).

Community Cloud runs the app outside your AWS account, so it needs explicit
credentials. Rather than reuse a broad profile, this creates a dedicated user
whose ONLY permissions are the Bedrock actions this app performs against ONE
knowledge base and the specific models it uses. If the key ever leaks, the blast
radius is "can query this one KB" — nothing else. Rotate or delete anytime.

What it grants (least privilege):
  * bedrock:Retrieve, bedrock:RetrieveAndGenerate   -> on the KB ARN only
  * bedrock:InvokeModel, bedrock:InvokeModelWithResponseStream
        -> on the generation model (Haiku inference profile + its foundation
           model) and the Titan embeddings model only

Usage:
    python infra/create_app_user.py --profile abulele-ntt-sandbox --region us-east-1 \
        --kb-id RG1JAFJZZX

Prints an access key ONCE. Paste the printed TOML into Community Cloud secrets.
Re-running rotates the key (old keys for this user are deleted first).
"""

import argparse
import json
import sys


DEFAULT_PROFILE = "abulele-ntt-sandbox"
DEFAULT_REGION = "us-east-1"
DEFAULT_USER = "growthpoint-rag-app"
GEN_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
GEN_FOUNDATION_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"


def policy_document(region: str, account: str, kb_id: str) -> dict:
    kb_arn = f"arn:aws:bedrock:{region}:{account}:knowledge-base/{kb_id}"
    # Generation runs through a cross-region inference profile; grant the profile
    # ARN AND the underlying foundation-model ARNs it may route to (us-east-1 and
    # the regional variants are covered by the wildcard region segment).
    gen_profile_arn = f"arn:aws:bedrock:{region}:{account}:inference-profile/{GEN_MODEL_ID}"
    gen_fm_arn = f"arn:aws:bedrock:*::foundation-model/{GEN_FOUNDATION_MODEL}"
    embed_fm_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBED_MODEL_ID}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "QueryKnowledgeBase",
                "Effect": "Allow",
                "Action": [
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate",
                ],
                "Resource": kb_arn,
            },
            {
                "Sid": "InvokeModels",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [gen_profile_arn, gen_fm_arn, embed_fm_arn],
            },
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="create_app_user.py",
        description="Create a scoped IAM user + key for Streamlit Community Cloud.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="Admin AWS profile to create the user with.")
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--kb-id", required=True, help="Knowledge Base ID the app may query.")
    p.add_argument("--user", default=DEFAULT_USER, help="IAM user name to create/reuse.")
    p.add_argument("--dry-run", action="store_true", help="Print the policy without any AWS call.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        print(json.dumps(policy_document(args.region, "ACCOUNT_ID", args.kb_id), indent=2))
        return 0

    import boto3
    from botocore.exceptions import ClientError

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    iam = session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]
    policy = policy_document(args.region, account, args.kb_id)
    policy_name = f"{args.user}-policy"

    # 1. Create (or reuse) the user.
    try:
        iam.create_user(UserName=args.user, Tags=[{"Key": "purpose", "Value": "growthpoint-rag-streamlit"}])
        print(f"Created IAM user {args.user}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"Reusing IAM user {args.user}")
        else:
            raise

    # 2. Attach the least-privilege inline policy.
    iam.put_user_policy(
        UserName=args.user,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy),
    )
    print(f"Attached least-privilege policy {policy_name}")

    # 3. Rotate: delete any existing access keys so we don't exceed the 2-key limit.
    for k in iam.list_access_keys(UserName=args.user).get("AccessKeyMetadata", []):
        iam.delete_access_key(UserName=args.user, AccessKeyId=k["AccessKeyId"])
        print(f"Deleted old access key {k['AccessKeyId']}")

    # 4. Create a fresh access key (shown ONCE).
    key = iam.create_access_key(UserName=args.user)["AccessKey"]

    print("\n" + "=" * 64)
    print("Paste this into Streamlit Community Cloud -> Settings -> Secrets:")
    print("=" * 64)
    print(f'USE_MOCK = "false"')
    print(f'AWS_REGION = "{args.region}"')
    print(f'KNOWLEDGE_BASE_ID = "{args.kb_id}"')
    print(f'LLM_MODEL_ID = "{GEN_MODEL_ID}"')
    print(f'AWS_ACCESS_KEY_ID = "{key["AccessKeyId"]}"')
    print(f'AWS_SECRET_ACCESS_KEY = "{key["SecretAccessKey"]}"')
    print("=" * 64)
    print("This secret key is shown ONCE. Store it now; re-run to rotate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

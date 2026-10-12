# Growthpoint Knowledge Assistant — RAG PoC

A proof-of-concept retrieval-augmented generation (RAG) assistant for **Growthpoint**,
styled to the brand ("creating space to thrive"). It answers questions from a
closed set of internal-style documents — policies, lease and property documents,
SOPs and technical runbooks — and shows its sources on every answer.

> **This PoC ships with mock data.** All documents in `src/mock/documents/corpus.json`
> are fictional samples written for demonstration. Swap in real content later, or
> point the app at a live AWS Bedrock Knowledge Base (see below).

## What it does

- **Answers from your content** — policies, leases, SOPs and runbooks, retrieved not guessed.
- **Shows its working** — every answer carries its source (document, reference, section, relevance).
- **Respects who is asking** — permission-aware retrieval: a user only sees what their role is entitled to.
- **Improves with use** — thumbs up / "needs work" feedback surfaces gaps and good answers.

## Guardrails (built in)

- **Closed corpus** — only the local documents are used; there is no open-web fallback.
- **No public training** — content never leaves the app's boundary.
- **Cited or silent** — if there is no supporting source, the assistant declines rather than guessing.

## Run it locally

```bash
cd growthpoint-rag-bot
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Then open http://localhost:8501. It runs in **mock mode** by default — no AWS, no credentials.

Use the **Access profile** selector in the sidebar (Tenant / Public, Growthpoint
Staff, Management) to see permission-aware retrieval in action. For example, lease
operating-cost detail is staff-level, and the arrears policy is management-only.

> Python 3.11 or 3.12 is recommended. On Python 3.14 some Streamlit dependencies
> have no prebuilt wheels yet and try to compile from source.

## Project structure

```
growthpoint-rag-bot/
├── app.py                         # entry point: streamlit run app.py
├── requirements.txt
├── .env.example                   # mock vs. live Bedrock settings
└── src/
    ├── config.py                  # branding, access levels, mock/live switch
    ├── models/chat_models.py      # Source + ChatMessage data models
    ├── clients/
    │   ├── mock_client.py         # local keyword retrieval over the closed corpus
    │   └── bedrock_client.py      # live AWS Bedrock Knowledge Base path
    ├── services/rag_service.py    # picks mock or live client, exposes chat()
    ├── frontend/streamlit_app.py  # branded UI, role selector, cited sources, feedback
    └── mock/documents/corpus.json # fictional sample Growthpoint documents
```

## Going live with AWS Bedrock + S3 Vectors

The mock and live paths share the same interface, so switching is mostly
provisioning + configuration. The live stack uses:

- **Vector store:** Amazon **S3 Vectors** (a vector bucket + vector index) — not OpenSearch.
- **Embeddings:** Amazon **Titan Text Embeddings V2** (1024-d, cosine distance).
- **Generation:** **Claude 3 Haiku** (`anthropic.claude-3-haiku-20240307-v1:0`) — the cheapest Claude tier.
- **Region:** **us-east-1** by default, because S3 Vectors + Bedrock Knowledge Bases
  are reliably available there (af-south-1 is not reliable for this combination).
- **Profile:** **abulele-ntt-sandbox** by default, overridable via `AWS_PROFILE` or `--profile`.

One-time provisioning (creates the S3 source bucket, uploads the corpus with
per-document `access_level` metadata, creates the S3 Vectors bucket + index, the
IAM role, the Knowledge Base and data source, and runs ingestion):

```bash
# 1. Sign in (default profile is abulele-ntt-sandbox)
aws sso login --profile abulele-ntt-sandbox

# 2. Install dependencies (boto3 new enough for the s3vectors client)
.venv/bin/pip install -r requirements.txt

# 3. See the plan without touching AWS (optional)
.venv/bin/python infra/provision.py --dry-run

# 4. Provision for real (defaults: --profile abulele-ntt-sandbox --region us-east-1)
.venv/bin/python infra/provision.py
```

The script prints `KNOWLEDGE_BASE_ID` and the exact env vars, and writes `.env`
if one does not already exist. Then set those env vars (or `source`/copy `.env`)
and run the app live:

```bash
export USE_MOCK=false
export AWS_REGION=us-east-1
export AWS_PROFILE=abulele-ntt-sandbox
export KNOWLEDGE_BASE_ID=<printed id>
export LLM_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
.venv/bin/streamlit run app.py
```

Permission-aware retrieval still applies in live mode: each document's
`access_level` is ingested as filterable metadata and the client applies a Bedrock
`in` filter so a user only retrieves documents at their level and below. Cited-or-silent
also holds: if Bedrock returns no citations, the assistant declines rather than guessing.

## Deploying for the customer to test

For a quick shareable demo, Streamlit Community Cloud or AWS App Runner host this
app directly. For AWS Amplify, run it behind a small container (Amplify Hosting
serves static sites; a Streamlit app needs a server runtime via App Runner, ECS,
or an Amplify-fronted backend). See `DEPLOYMENT.md` for the recommended options.
```

## Note on data

Everything in the knowledge base is sample data created for this PoC. Replace the
documents in `src/mock/documents/corpus.json` (or ingest real content into Bedrock)
when you move beyond the demo.

# Deployment notes

The PoC is a Streamlit app, which needs a running server (not static hosting). A
few options, easiest first.

## Option 1 — Streamlit Community Cloud (fastest to share)

1. Push `growthpoint-rag-bot/` to a GitHub repo.
2. Go to share.streamlit.io, connect the repo, set the main file to `app.py`.
3. It installs `requirements.txt` and gives you a public URL to send the customer.

Mock mode needs no secrets. For live Bedrock, add the env vars under the app's
Secrets settings.

## Option 2 — AWS App Runner (AWS-native, container)

Streamlit needs a server runtime, so on AWS the clean path is a container on
App Runner (or ECS Fargate). A minimal `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Build, push to ECR, and create an App Runner service from the image. Set env vars
(`USE_MOCK`, `KNOWLEDGE_BASE_ID`, `AWS_REGION=us-east-1`, `AWS_PROFILE=abulele-ntt-sandbox`,
`LLM_MODEL_ID`) in the service config, and give the App Runner instance role Bedrock
permissions when using the live path. The live vector store is Amazon S3 Vectors
(not OpenSearch); provision it first with `python infra/provision.py` (see README).

## Option 3 — Amplify

Amplify Hosting is designed for static front-ends and serverless backends, not a
long-running Streamlit server. To use Amplify, you would either:

- Rebuild the UI as a static front-end (e.g. React) calling a backend API (API
  Gateway + Lambda) that does the retrieve-and-generate, then host the front-end
  on Amplify; or
- Keep this Streamlit app on App Runner / ECS and only use Amplify for a marketing
  or landing page in front of it.

For a quick customer test, Option 1 or 2 gets you a working link with the least
effort. We can wire up Amplify once the UX is signed off.
```

"""
AWS Bedrock Knowledge Base client (live mode).

Only used when USE_MOCK=false and a KNOWLEDGE_BASE_ID is configured. The PoC runs
in mock mode by default, so this path is optional. It mirrors the mock client's
interface so the rest of the app does not care which one is in use.

Vector store: Amazon S3 Vectors (provisioned by infra/provision.py). Embeddings:
Amazon Titan Text Embeddings V2. Generation: cheapest Claude (Claude 3 Haiku).

Guardrails enforced here, matching the mock client:
  * Closed corpus + cited-or-silent via the generation prompt template: the model
    answers ONLY from the retrieved search results and declines (no guessing) when
    the context does not contain the answer. If Bedrock returns no citations, we
    set answered=False.
  * Permission-aware retrieval via a Knowledge Base metadata filter on an
    `access_level` attribute. Each ingested document carries an `access_level`
    metadata field of "public", "staff" or "management"; a user retrieves only
    documents at their level and below.
"""

import time
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

from models.chat_models import Source
from config import Config


# Closed-corpus, cited-or-silent prompt template. The model must answer only from
# $search_results$ and decline rather than guess when the answer is not present.
_PROMPT_TEMPLATE = (
    "You are the Growthpoint Knowledge Assistant. Answer the question using ONLY "
    "the information in the search results below. Do not use any outside knowledge "
    "and do not guess. If the search results do not contain the answer, reply that "
    "you cannot find it in the Growthpoint knowledge base and ask the user to "
    "rephrase or contact the relevant team. Keep answers concise and grounded in "
    "the cited sources.\n\n"
    "Search results:\n$search_results$\n\n"
    "Question: $query$"
)


class BedrockKnowledgeBaseClient:
    """Client for AWS Bedrock Knowledge Base retrieve-and-generate (S3 Vectors)."""

    def __init__(self, config: Config):
        self.config = config
        # One session, shared by both clients, so profile/region are consistent.
        # Use the named profile for local development, but fall back to ambient
        # credentials (access keys / instance role) when access keys are present
        # in the environment — e.g. on Streamlit Community Cloud, where the named
        # profile does not exist. This keeps `aws_profile` for local use without
        # breaking hosted deployments.
        import os

        if os.getenv("AWS_ACCESS_KEY_ID"):
            self._session = boto3.Session(region_name=config.aws_region)
        else:
            self._session = boto3.Session(
                region_name=config.aws_region,
                profile_name=config.aws_profile,
            )
        self.bedrock_agent_runtime = self._session.client("bedrock-agent-runtime")
        # Lazily created to confirm the KB exists / resolve the account id cheaply.
        self._bedrock_agent = None
        self._account_id = None

    def _agent_client(self):
        if self._bedrock_agent is None:
            self._bedrock_agent = self._session.client("bedrock-agent")
        return self._bedrock_agent

    def _get_account_id(self) -> str:
        if self._account_id is None:
            self._account_id = self._session.client("sts").get_caller_identity()["Account"]
        return self._account_id

    def _model_arn(self) -> str:
        """Build the correct ARN for the configured model id.

        A cross-region inference profile id (e.g. "us.anthropic..." or
        "global.anthropic...") resolves to an inference-profile ARN that includes
        the account id. A plain foundation-model id resolves to a region-only
        foundation-model ARN. Many current Claude models (incl. Haiku 4.5) are
        ONLY served via an inference profile.
        """
        model_id = self.config.llm_model_id
        region = self.config.aws_region
        if model_id.startswith(("us.", "eu.", "apac.", "global.")):
            account = self._get_account_id()
            return (
                f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}"
            )
        return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"

    def _access_filter(self, user_level: str) -> Dict[str, Any]:
        """Bedrock retrieval filter: access_level in [levels at or below the user].

        public => [public]; staff => [public, staff];
        management => [public, staff, management].
        """
        levels = self.config.access_levels
        if user_level not in levels:
            user_level = self.config.default_access_level
        allowed = levels[: levels.index(user_level) + 1]
        return {"in": {"key": "access_level", "value": allowed}}

    def _retrieve(self, query: str, user_level: str) -> List[Source]:
        """Deterministic retrieval with the permission filter applied.

        Used to decide "cited or silent" independently of whether the generation
        step emits citations (a custom prompt template can suppress them), and to
        provide the source cards shown in the UI.
        """
        resp = self.bedrock_agent_runtime.retrieve(
            knowledgeBaseId=self.config.knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": self.config.num_results,
                    "filter": self._access_filter(user_level),
                }
            },
        )
        sources: List[Source] = []
        for res in resp.get("retrievalResults", []) or []:
            meta = res.get("metadata", {}) or {}
            content = res.get("content", {}) or {}
            try:
                score = float(res.get("score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            sources.append(
                Source(
                    content=str(content.get("text", "") or ""),
                    title=str(meta.get("title", "Growthpoint document")),
                    category=str(meta.get("category", "Document")),
                    doc_ref=str(meta.get("doc_ref", "")),
                    section=str(meta.get("section", "")),
                    access_level=str(meta.get("access_level", "public")),
                    score=score,
                )
            )
        return sources

    def retrieve_and_generate(
        self, query: str, user_level: str, chat_history=None
    ) -> Dict[str, Any]:
        start = time.time()
        try:
            # Step 1: deterministic retrieval decides cited-or-silent and gives
            # us the sources to display. The permission filter is applied here.
            sources = self._retrieve(query, user_level)
            if not sources:
                return {
                    "response": (
                        "I can't find anything in the Growthpoint knowledge base to "
                        "answer that, so I won't guess. It may be outside the current "
                        "document set or above your access level."
                    ),
                    "sources": [],
                    "answered": False,
                    "generation_time_ms": (time.time() - start) * 1000,
                    "success": True,
                }

            # Step 2: generate a grounded answer. Closed-corpus / no-guessing is
            # enforced by the prompt template; the same permission filter is used.
            request = {
                "input": {"text": query},
                "retrieveAndGenerateConfiguration": {
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": self.config.knowledge_base_id,
                        "modelArn": self._model_arn(),
                        "retrievalConfiguration": {
                            "vectorSearchConfiguration": {
                                "numberOfResults": self.config.num_results,
                                "filter": self._access_filter(user_level),
                            }
                        },
                        "generationConfiguration": {
                            "promptTemplate": {"textPromptTemplate": _PROMPT_TEMPLATE}
                        },
                    },
                },
            }
            response = self.bedrock_agent_runtime.retrieve_and_generate(**request)
            answer = response.get("output", {}).get("text", "")

            # The retrieved chunks may be only loosely related (vector search
            # always returns its nearest neighbours), so the model, following the
            # closed-corpus prompt, can still correctly decline. Detect that
            # refusal and treat it as "silent": answered=False and no source cards,
            # so the UI does not imply the answer came from those documents.
            if self._is_refusal(answer):
                return {
                    "response": answer,
                    "sources": [],
                    "answered": False,
                    "generation_time_ms": (time.time() - start) * 1000,
                    "success": True,
                }

            # Prefer citation-derived sources when present (they reflect what the
            # model actually used); otherwise fall back to the retrieved sources.
            cited = self._parse_citations(response)
            display_sources = cited or sources

            return {
                "response": answer,
                "sources": display_sources[:3],
                "answered": True,
                "generation_time_ms": (time.time() - start) * 1000,
                "success": True,
            }
        except ClientError as e:
            return {
                "response": f"I encountered an error reaching the knowledge base: {e}",
                "sources": [],
                "answered": False,
                "generation_time_ms": (time.time() - start) * 1000,
                "success": False,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Streaming (Converse streaming API) so the UI shows the answer forming.
    # ------------------------------------------------------------------
    def _runtime_client(self):
        if getattr(self, "_bedrock_runtime", None) is None:
            self._bedrock_runtime = self._session.client("bedrock-runtime")
        return self._bedrock_runtime

    @staticmethod
    def _format_context(sources: List[Source]) -> str:
        """Render retrieved sources as the grounding context for Converse."""
        blocks = []
        for i, s in enumerate(sources, 1):
            blocks.append(
                f"[{i}] {s.title} ({s.doc_ref}, {s.section})\n{s.content}"
            )
        return "\n\n".join(blocks)

    def retrieve_and_generate_stream(self, query: str, user_level: str, chat_history=None):
        """Streaming RAG. Yields event dicts in order:

            {"type": "sources", "sources": [...], "answered": bool}
            {"type": "delta",   "text": "..."}       # zero or more
            {"type": "done",    "answered": bool, "sources": [...]}
            {"type": "error",   "text": "..."}

        Retrieval (with the permission filter) runs first so the UI can show the
        source cards and the "silent" verdict immediately; the answer is then
        streamed token-by-token via the Bedrock Converse streaming API, keeping
        the closed-corpus / no-guessing guardrail in the system prompt.
        """
        try:
            sources = self._retrieve(query, user_level)
            if not sources:
                msg = (
                    "I can't find anything in the Growthpoint knowledge base to "
                    "answer that, so I won't guess. It may be outside the current "
                    "document set or above your access level."
                )
                yield {"type": "sources", "sources": [], "answered": False}
                yield {"type": "delta", "text": msg}
                yield {"type": "done", "answered": False, "sources": []}
                return

            # Sources known up front (shown while the answer streams).
            yield {"type": "sources", "sources": sources[:3], "answered": True}

            system_prompt = (
                "You are the Growthpoint Knowledge Assistant. Answer the question "
                "using ONLY the information in the provided context. Do not use any "
                "outside knowledge and do not guess. If the context does not contain "
                "the answer, reply that you cannot find it in the Growthpoint "
                "knowledge base and ask the user to rephrase or contact the relevant "
                "team. Keep answers concise and grounded in the sources."
            )
            user_text = (
                f"Context:\n{self._format_context(sources)}\n\nQuestion: {query}"
            )

            resp = self._runtime_client().converse_stream(
                modelId=self.config.llm_model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                # Some Claude models (e.g. Haiku 4.5) reject temperature and topP
                # together; send only temperature.
                inferenceConfig={
                    "maxTokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                },
            )

            full = []
            for event in resp["stream"]:
                if "contentBlockDelta" in event:
                    piece = event["contentBlockDelta"]["delta"].get("text", "")
                    if piece:
                        full.append(piece)
                        yield {"type": "delta", "text": piece}

            answer = "".join(full)
            # If the grounded answer is actually a refusal, present it as silent.
            if self._is_refusal(answer):
                yield {"type": "done", "answered": False, "sources": []}
            else:
                yield {"type": "done", "answered": True, "sources": sources[:3]}
        except ClientError as e:
            yield {"type": "error", "text": f"I encountered an error reaching the knowledge base: {e}"}

    @staticmethod
    def _is_refusal(answer: str) -> bool:
        """True if the model's answer is a closed-corpus 'I cannot find it' reply.

        The prompt template steers refusals to say the information is not in the
        Growthpoint knowledge base; detect those so we present them as silent.
        """
        if not answer:
            return True
        low = answer.lower()
        refusal_markers = (
            "cannot find",
            "can't find",
            "could not find",
            "couldn't find",
            "don't have information",
            "do not have information",
            "no information",
            "not contain",
        )
        return any(m in low for m in refusal_markers)

    @staticmethod
    def _parse_citations(response: Dict[str, Any]) -> List[Source]:
        """Build Source objects from citation retrievedReferences, defensively."""
        sources: List[Source] = []
        for citation in response.get("citations", []) or []:
            for ref in citation.get("retrievedReferences", []) or []:
                meta = ref.get("metadata", {}) or {}
                content = ref.get("content", {}) or {}
                # Relevance score, when present, is on the reference (not metadata).
                raw_score = ref.get("score", meta.get("score", 0.0))
                try:
                    score = float(raw_score or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                sources.append(
                    Source(
                        content=str(content.get("text", "") or ""),
                        title=str(meta.get("title", "Growthpoint document")),
                        category=str(meta.get("category", "Document")),
                        doc_ref=str(meta.get("doc_ref", "")),
                        section=str(meta.get("section", "")),
                        access_level=str(meta.get("access_level", "public")),
                        score=score,
                    )
                )
        return sources

    def test_connection(self) -> Dict[str, Any]:
        """Confirm the KB is reachable without depending on a non-empty corpus.

        Uses a lightweight bedrock-agent GetKnowledgeBase call; it succeeds even
        when the KB has no documents yet. Never raises.
        """
        kb_id = self.config.knowledge_base_id
        try:
            if not kb_id:
                return {
                    "success": False,
                    "mode": "bedrock",
                    "knowledge_base_id": kb_id,
                    "error": "KNOWLEDGE_BASE_ID is not set.",
                }
            self._agent_client().get_knowledge_base(knowledgeBaseId=kb_id)
            return {
                "success": True,
                "mode": "bedrock",
                "knowledge_base_id": kb_id,
            }
        except Exception as e:  # noqa: BLE001 - status probe must never raise
            return {
                "success": False,
                "mode": "bedrock",
                "knowledge_base_id": kb_id,
                "error": str(e),
            }

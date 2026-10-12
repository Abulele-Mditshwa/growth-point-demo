"""
Mock knowledge-base client for the Growthpoint RAG PoC.

Runs with NO AWS dependency. It performs lightweight keyword retrieval over a
local, closed corpus of sample Growthpoint documents and composes a cited answer.

It deliberately enforces the same guardrails the real system would:
  * Closed corpus      - only the local documents are ever used; no open web.
  * Permission-aware   - documents above the user's access level are never retrieved.
  * Cited or silent    - if nothing relevant is found, the assistant declines
                         rather than guessing.
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, Any, List

from models.chat_models import Source
from config import Config


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "what", "how", "do", "does", "can", "i", "my", "we", "our", "you", "your",
    "me", "with", "at", "by", "it", "this", "that", "about", "as", "be", "if",
    "when", "where", "which", "who", "will", "would", "should", "there", "here",
    "please", "tell", "explain", "give", "need", "want", "am",
}

CORPUS_PATH = Path(__file__).resolve().parent.parent / "mock" / "documents" / "corpus.json"


class MockKnowledgeBaseClient:
    """Keyword-retrieval client over the local mock corpus."""

    def __init__(self, config: Config):
        self.config = config
        self._documents: List[Dict[str, Any]] = self._load_corpus()

    # ------------------------------------------------------------------
    # Corpus loading
    # ------------------------------------------------------------------
    def _load_corpus(self) -> List[Dict[str, Any]]:
        with open(CORPUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("documents", [])

    @property
    def document_count(self) -> int:
        return len(self._documents)

    # ------------------------------------------------------------------
    # Permission helpers
    # ------------------------------------------------------------------
    def _allowed_levels(self, user_level: str) -> List[str]:
        levels = self.config.access_levels
        if user_level not in levels:
            user_level = self.config.default_access_level
        idx = levels.index(user_level)
        return levels[: idx + 1]

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        words = re.findall(r"[a-zA-Z]+", text.lower())
        return [w for w in words if w not in _STOPWORDS and len(w) > 2]

    @staticmethod
    def _token_match(qt: str, doc_tokens: set) -> bool:
        """Match a query token against document tokens.

        Exact match, or a shared stem of at least 4 characters, so short generic
        overlaps (e.g. 'are', 'how') don't create spurious hits.
        """
        if qt in doc_tokens:
            return True
        if len(qt) < 4:
            return False
        return any(
            (len(dt) >= 4 and (qt.startswith(dt) or dt.startswith(qt)))
            for dt in doc_tokens
        )

    def _score_document(self, query_tokens: List[str], doc: Dict[str, Any]) -> float:
        if not query_tokens:
            return 0.0
        body_tokens = set(self._tokenize(f"{doc['category']} {doc['content']}"))
        heading_tokens = set(self._tokenize(f"{doc['title']} {doc['section']}"))
        all_tokens = body_tokens | heading_tokens

        hits = sum(1 for qt in query_tokens if self._token_match(qt, all_tokens))
        base = hits / len(query_tokens)

        # Heading matches are strong topical signals; weight them heavily.
        heading_hits = sum(1 for qt in query_tokens if self._token_match(qt, heading_tokens))
        heading_bonus = 0.35 * (heading_hits / len(query_tokens))

        return base + heading_bonus

    def retrieve(self, query: str, user_level: str, max_results: int) -> List[Source]:
        allowed = self._allowed_levels(user_level)
        query_tokens = self._tokenize(query)

        scored = []
        for doc in self._documents:
            if doc["access_level"] not in allowed:
                continue  # permission-aware: skip documents above the user's level
            score = self._score_document(query_tokens, doc)
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]

        sources: List[Source] = []
        for score, doc in top:
            sources.append(
                Source(
                    content=doc["content"],
                    title=doc["title"],
                    category=doc["category"],
                    doc_ref=doc["doc_ref"],
                    section=doc["section"],
                    access_level=doc["access_level"],
                    score=round(min(score, 1.0), 3),
                )
            )
        return sources

    # ------------------------------------------------------------------
    # Answer composition
    # ------------------------------------------------------------------
    def retrieve_and_generate(
        self, query: str, user_level: str, chat_history=None
    ) -> Dict[str, Any]:
        start = time.time()
        sources = self.retrieve(query, user_level, self.config.num_results)

        # Cited or silent: only keep sources that clear the confidence bar, and
        # only answer if the single best source is genuinely on-topic.
        CONFIDENCE = 0.5
        strong = [s for s in sources if s.score >= CONFIDENCE]
        if not strong or sources[0].score < CONFIDENCE:
            # Check whether something exists but is above the user's permission.
            restricted = self._restricted_match_exists(query, user_level)
            if restricted:
                answer = (
                    "There is related information in the knowledge base, but it sits "
                    "in documents your current access level is not permitted to see. "
                    "Please check with your manager or raise an access request."
                )
            else:
                answer = (
                    "I couldn't find anything in the Growthpoint knowledge base to answer "
                    "that with confidence, so I won't guess. Try rephrasing, or ask about "
                    "leases, operating costs, parking, maintenance, sustainability, "
                    "evacuation, HVAC or loadshedding procedures."
                )
            return {
                "response": answer,
                "sources": [],
                "answered": False,
                "generation_time_ms": (time.time() - start) * 1000,
                "success": True,
            }

        answer = self._compose_answer(query, strong)
        return {
            "response": answer,
            "sources": strong[:3],
            "answered": True,
            "generation_time_ms": (time.time() - start) * 1000,
            "success": True,
        }

    def _restricted_match_exists(self, query: str, user_level: str) -> bool:
        allowed = self._allowed_levels(user_level)
        query_tokens = self._tokenize(query)
        for doc in self._documents:
            if doc["access_level"] in allowed:
                continue
            if self._score_document(query_tokens, doc) >= 0.5:
                return True
        return False

    @staticmethod
    def _first_sentences(text: str, n: int = 2) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        return " ".join(parts[:n]).strip()

    def _compose_answer(self, query: str, sources: List[Source]) -> str:
        """Compose a grounded, extractive answer from the top sources.

        The PoC does not call an LLM in mock mode; it summarises the retrieved
        passages so every statement is traceable to a cited source.
        """
        lead = self._first_sentences(sources[0].content, 2)
        answer = lead
        if len(sources) > 1:
            extra = self._first_sentences(sources[1].content, 1)
            if extra and extra not in answer:
                answer += " " + extra
        return answer

    # ------------------------------------------------------------------
    # Streaming (simulated) so the demo mode matches the live streaming UX.
    # ------------------------------------------------------------------
    def retrieve_and_generate_stream(self, query: str, user_level: str, chat_history=None):
        """Yield the same event protocol as the live client, simulating streaming.

        Mock mode has no LLM, so it composes the answer, then emits it in small
        chunks with a short delay to mimic a model "typing".
        """
        import time as _time

        result = self.retrieve_and_generate(query, user_level, chat_history)
        answered = result["answered"]
        sources = result.get("sources") or []

        yield {"type": "sources", "sources": sources, "answered": answered}

        words = result["response"].split(" ")
        for i in range(0, len(words), 2):
            chunk = " ".join(words[i : i + 2])
            if i + 2 < len(words):
                chunk += " "
            yield {"type": "delta", "text": chunk}
            _time.sleep(0.03)

        yield {"type": "done", "answered": answered, "sources": sources}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def test_connection(self) -> Dict[str, Any]:
        return {
            "success": True,
            "mode": "mock",
            "documents_indexed": self.document_count,
            "knowledge_base_id": "mock-local-corpus",
        }

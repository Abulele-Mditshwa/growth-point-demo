"""
Configuration for the Growthpoint Knowledge Assistant (RAG PoC).

This PoC ships in MOCK MODE by default so it runs with zero AWS setup. In mock
mode answers and citations are served from a local, closed corpus of sample
Growthpoint documents (see src/mock/documents/corpus.json).

On Streamlit Community Cloud, config is supplied via st.secrets (see
.streamlit/secrets.toml.example); those values are copied into the environment
in load_config() so the same env-var logic applies everywhere.

To switch to a live AWS Bedrock Knowledge Base, set USE_MOCK=false and provide your
own KNOWLEDGE_BASE_ID. Everything else (UI, guardrails, citation behaviour) is
identical in both modes.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

# Load a .env file (if present) so `streamlit run app.py` picks up live settings
# such as USE_MOCK=false and KNOWLEDGE_BASE_ID without manual exporting.
try:
    from dotenv import load_dotenv

    # Standard dotenv semantics: fill in values from .env but let an explicit
    # environment variable (e.g. an exported USE_MOCK) take precedence, so the
    # user can flip to mock mode without editing .env.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except Exception:  # noqa: BLE001 - dotenv is optional; mock mode needs no .env
    pass


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Settings for the Growthpoint Knowledge Assistant."""

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------
    # Mock mode = no AWS required. Perfect for the PoC / customer demo.
    use_mock: bool = _env_bool("USE_MOCK", True)

    # ------------------------------------------------------------------
    # Branding (Growthpoint "creating space to thrive")
    # ------------------------------------------------------------------
    brand_name: str = "Growthpoint"
    app_name: str = "Growthpoint Knowledge Assistant"
    app_tagline: str = "Answers from your documents. Cited, closed-corpus, permission-aware."
    # Growthpoint corporate identity: deep aubergine/plum (from the official logo,
    # #470635) with a warm magenta accent.
    brand_primary: str = "#470635"    # Growthpoint aubergine
    brand_primary_dark: str = "#2E0422"
    brand_accent: str = "#A4136C"     # warm magenta accent
    brand_accent_soft: str = "#F7E9F1"
    brand_ink: str = "#2E0422"        # plum ink
    brand_muted: str = "#7A6B74"

    # ------------------------------------------------------------------
    # Permission-aware retrieval
    # ------------------------------------------------------------------
    # Access levels in increasing order of privilege. A user at a given level
    # can see documents at their level and below.
    access_levels: List[str] = field(default_factory=lambda: ["public", "staff", "management"])
    default_access_level: str = "public"

    # ------------------------------------------------------------------
    # AWS Bedrock (only used when use_mock = False)
    # ------------------------------------------------------------------
    # us-east-1 is the default because Amazon S3 Vectors and Bedrock Knowledge
    # Bases are reliably available there; af-south-1 is NOT reliable for this
    # combination. Override with AWS_REGION if your account differs.
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    # Default sandbox profile; overridable via AWS_PROFILE. This is the single
    # authoritative place the app reads the profile.
    aws_profile: Optional[str] = os.getenv("AWS_PROFILE", "abulele-ntt-sandbox")
    knowledge_base_id: str = os.getenv("KNOWLEDGE_BASE_ID", "")
    # Cheapest ACTIVE Claude on Bedrock. Claude 3 Haiku reached end-of-life
    # (Sept 2026), so the current cheapest Haiku is 4.5, which is only served via
    # an inference profile (the "us." prefix routes a US cross-region profile).
    # Configurable via LLM_MODEL_ID; a newer/cheaper Haiku can be swapped in later.
    llm_model_id: str = os.getenv(
        "LLM_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )

    # ------------------------------------------------------------------
    # Embeddings (Amazon Titan Text Embeddings V2 for the S3 Vectors KB)
    # ------------------------------------------------------------------
    # Titan V2 at 1024 dimensions with cosine distance (S3 Vectors + Titan V2).
    embedding_model_id: str = os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    embedding_distance_metric: str = os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine")

    # Generation parameters
    max_tokens: int = 1000
    temperature: float = 0.1
    top_p: float = 0.9
    num_results: int = 5


_SECRET_KEYS = (
    "USE_MOCK", "AWS_REGION", "AWS_PROFILE", "KNOWLEDGE_BASE_ID", "LLM_MODEL_ID",
    "EMBEDDING_MODEL_ID", "EMBEDDING_DIMENSION", "EMBEDDING_DISTANCE_METRIC",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
)


def _load_streamlit_secrets() -> None:
    """On Streamlit Community Cloud there is no shell env or .env file; config is
    provided via st.secrets. Copy any known keys into os.environ (without
    overriding an already-set env var) so the rest of config works unchanged.
    """
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if not secrets:
            return
        for key in _SECRET_KEYS:
            if key in secrets and os.getenv(key) is None:
                os.environ[key] = str(secrets[key])
    except Exception:  # noqa: BLE001 - secrets are optional (local/dev)
        pass


def load_config() -> Config:
    """Load configuration (st.secrets < .env < environment variables)."""
    _load_streamlit_secrets()
    return Config()

"""
Growthpoint Knowledge Assistant — RAG PoC entry point.

Run with:
    streamlit run app.py

Runs in mock mode by default (no AWS required). To use a live AWS Bedrock
Knowledge Base, set USE_MOCK=false and KNOWLEDGE_BASE_ID in your environment.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from frontend.streamlit_app import main  # noqa: E402

if __name__ == "__main__":
    main()

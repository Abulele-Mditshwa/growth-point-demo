"""Quick logic smoke test for the mock RAG pipeline (no Streamlit)."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent / "src"))

from config import load_config
from services.rag_service import RAGService

cfg = load_config()
rag = RAGService(cfg)
print("MODE:", rag.mode, "| status:", rag.status())

def ask(q, level):
    r = rag.chat(q, level)
    titles = [s.title for s in (r.sources or [])]
    print(f"\n[{level}] Q: {q}")
    print(f"  answered={r.answered} sources={titles}")
    print(f"  A: {r.content[:160]}")

# 1. Public question -> should answer with a public source
ask("How is tenant parking allocated?", "public")
# 2. Operating costs is a staff-level lease doc -> public should NOT get it
ask("How are operating costs calculated in an office lease?", "public")
# 3. Same question as staff -> should answer with the lease source
ask("How are operating costs calculated in an office lease?", "staff")
# 4. Management-only arrears policy -> staff should be blocked
ask("What is the arrears and credit control collection process?", "staff")
# 5. Management-only arrears policy -> management should answer
ask("What is the arrears and credit control collection process?", "management")
# 6. Off-corpus -> cited or silent -> should decline
ask("What is the capital of France?", "management")
print("\nSMOKE_OK")

"""
Streamlit frontend for the Growthpoint Knowledge Assistant (RAG PoC).

Corporate navy/azure design with token-by-token streaming answers.
"""

import base64
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config import load_config
from models.chat_models import ChatMessage, MessageRole
from services.rag_service import RAGService


ROLE_LABELS = {
    "public": "Tenant / Public",
    "staff": "Growthpoint Staff",
    "management": "Management",
}

# Official Growthpoint logo, inlined as a data URI so it renders in raw HTML
# without any static-file-serving setup.
_LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "GrowthPoint-Logo (Global)-1.svg"


def _logo_data_uri() -> str:
    try:
        svg = _LOGO_PATH.read_bytes()
        b64 = base64.b64encode(svg).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:  # noqa: BLE001 - logo is cosmetic; degrade gracefully
        return ""


class GrowthpointApp:
    def __init__(self):
        self.config = load_config()
        self.rag = RAGService(self.config)
        self._init_state()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _init_state(self):
        if "messages" not in st.session_state:
            st.session_state.messages = []
        if "user_level" not in st.session_state:
            st.session_state.user_level = self.config.default_access_level
        if "feedback" not in st.session_state:
            st.session_state.feedback = {}
        if "pending" not in st.session_state:
            st.session_state.pending = None  # a prompt waiting to be streamed

    # ------------------------------------------------------------------
    # Page + styling
    # ------------------------------------------------------------------
    def page_config(self):
        st.set_page_config(
            page_title=self.config.app_name,
            page_icon="◆",
            layout="centered",
            initial_sidebar_state="expanded",
        )

    def inject_css(self):
        c = self.config
        st.markdown(
            f"""
            <style>
            @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600;700&display=swap');

            :root {{
                --nv: {c.brand_primary};
                --nv-dark: {c.brand_primary_dark};
                --az: {c.brand_accent};
                --az-soft: {c.brand_accent_soft};
                --ink: {c.brand_ink};
                --muted: {c.brand_muted};
            }}

            html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
            .stApp {{ background: #F6F8FB; }}
            #MainMenu, header[data-testid="stHeader"], footer {{ visibility: hidden; }}
            .block-container {{ padding-top: 1.2rem; max-width: 820px; }}

            /* ---------- top bar ---------- */
            .nv-topbar {{
                display:flex; align-items:center; justify-content:space-between;
                border-bottom: 1px solid #E3E8F0;
                padding: 0.2rem 0 1rem 0; margin-bottom: 1.2rem;
            }}
            .nv-brand {{ display:flex; align-items:center; gap:0.7rem; }}
            .nv-logo {{ height:46px; width:auto; display:block; }}
            .nv-mark {{
                width:38px; height:38px; border-radius:9px;
                background: var(--nv); color:#fff;
                display:flex; align-items:center; justify-content:center;
                font-weight:700; font-size:1.1rem; letter-spacing:-0.03em;
                box-shadow: inset 0 -3px 0 var(--az);
            }}
            .nv-brand-text b {{ color: var(--nv); font-size:1.05rem; font-weight:700; letter-spacing:-0.01em; }}
            .nv-brand-text span {{ display:block; color: var(--muted); font-size:0.72rem; letter-spacing:0.04em; }}
            .nv-pill {{
                font-size:0.72rem; font-weight:600; color: var(--nv);
                background: var(--az-soft); border:1px solid #CDE8F6;
                padding:0.3rem 0.7rem; border-radius:999px;
            }}

            /* ---------- hero ---------- */
            .nv-hero h1 {{
                font-family:'Fraunces', serif; font-weight:600;
                color: var(--nv); font-size:2.1rem; line-height:1.15;
                margin:0.2rem 0 0.4rem 0; letter-spacing:-0.01em;
            }}
            .nv-hero p {{ color: var(--muted); font-size:1rem; margin:0; max-width:60ch; }}
            .nv-rails {{ display:flex; gap:0.5rem; flex-wrap:wrap; margin:1rem 0 0.4rem 0; }}
            .nv-rail {{
                font-size:0.74rem; font-weight:600; color:#334155;
                background:#fff; border:1px solid #E3E8F0;
                padding:0.32rem 0.7rem; border-radius:8px;
            }}
            .nv-rail b {{ color: var(--az); }}

            hr.nv-sep {{ border:none; border-top:1px solid #E8ECF3; margin:1.3rem 0; }}

            /* ---------- chat ---------- */
            .nv-row {{ display:flex; gap:0.75rem; margin:1.1rem 0; align-items:flex-start; }}
            .nv-row.user {{ flex-direction:row-reverse; }}
            .nv-av {{
                flex:0 0 34px; width:34px; height:34px; border-radius:50%;
                display:flex; align-items:center; justify-content:center;
                font-size:0.82rem; font-weight:700;
            }}
            .nv-av.bot {{ background: var(--nv); color:#fff; }}
            .nv-av.me {{ background: var(--az-soft); color: var(--nv); border:1px solid #CDE8F6; }}
            .nv-bubble {{
                padding:0.85rem 1.05rem; border-radius:14px; line-height:1.55;
                font-size:0.96rem; color: var(--ink); max-width:88%;
            }}
            .nv-bubble.user {{ background: var(--nv); color:#fff; border-bottom-right-radius:4px; }}
            .nv-bubble.bot {{ background:#fff; border:1px solid #E6EBF2; border-bottom-left-radius:4px;
                              box-shadow:0 1px 3px rgba(21,40,75,0.05); }}
            .nv-bubble.silent {{ background:#FFF9EC; border:1px solid #F3E2B8; }}

            /* ---------- sources (compact, citation-style) ---------- */
            .nv-src-head {{ font-size:0.68rem; font-weight:700; letter-spacing:0.1em;
                            text-transform:uppercase; color: var(--muted);
                            margin:0.2rem 0 0.45rem 2.4rem; }}
            .nv-srclist {{ margin-left:2.4rem; display:flex; flex-direction:column; gap:0.35rem; }}
            .nv-src {{
                display:flex; gap:0.6rem; align-items:flex-start;
                background:#fff; border:1px solid #ECEFF4;
                border-radius:8px; padding:0.5rem 0.7rem;
            }}
            .nv-src-num {{
                flex:0 0 20px; width:20px; height:20px; margin-top:1px;
                border-radius:5px; background: var(--az-soft); color: var(--nv);
                font-size:0.68rem; font-weight:700;
                display:flex; align-items:center; justify-content:center;
            }}
            .nv-src-body {{ flex:1; min-width:0; }}
            .nv-src-title {{ font-weight:600; color: var(--nv); font-size:0.84rem; line-height:1.3; }}
            .nv-src-meta {{ font-size:0.7rem; color: var(--muted); margin-top:0.08rem; }}
            .nv-src-snip {{ font-size:0.78rem; color:#64748B; margin-top:0.25rem; line-height:1.45;
                            overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
            .nv-badge {{ display:inline-block; font-size:0.6rem; font-weight:700; padding:0.05rem 0.4rem;
                         border-radius:4px; margin-left:0.4rem; vertical-align:middle; text-transform:uppercase; letter-spacing:0.03em; }}
            .nv-badge.public {{ background:#E6F4FB; color:#0369A1; }}
            .nv-badge.staff {{ background:#EEF2FF; color:#4338CA; }}
            .nv-badge.management {{ background:#FEF2F2; color:#B91C1C; }}

            /* ---------- buttons / sidebar ---------- */
            .stButton > button {{
                background:#fff; color: var(--nv); border:1px solid #D7DEEA;
                border-radius:9px; padding:0.5rem 0.8rem; font-weight:500;
                font-size:0.86rem; width:100%; text-align:left; transition:all .12s ease;
            }}
            .stButton > button:hover {{ border-color: var(--az); color: var(--nv); background: var(--az-soft); }}

            /* native thumbs feedback: align under the answer, keep it subtle */
            .nv-fb + div [data-testid="stFeedback"] {{ margin-left:2.4rem; }}
            [data-testid="stFeedback"] button {{ color: var(--muted); }}
            [data-testid="stFeedback"] button:hover {{ color: var(--az); }}
            section[data-testid="stSidebar"] {{ background:#fff; border-right:1px solid #E3E8F0; }}
            section[data-testid="stSidebar"] h3 {{ color: var(--nv); font-size:0.95rem; }}
            [data-testid="stChatInput"] textarea {{ font-size:0.95rem; }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    # ------------------------------------------------------------------
    # Top bar + hero
    # ------------------------------------------------------------------
    def header(self):
        logo = _logo_data_uri()
        brand = (
            f'<img class="nv-logo" src="{logo}" alt="Growthpoint" />'
            if logo
            else '<div class="nv-mark">G</div>'
            '<div class="nv-brand-text"><b>Growthpoint</b>'
            '<span>Creating space to thrive</span></div>'
        )
        st.markdown(
            f"""
            <div class="nv-topbar">
                <div class="nv-brand">{brand}</div>
            </div>
            <div class="nv-hero">
                <h1>Knowledge Assistant</h1>
                <p>Ask about policies, leases, SOPs and technical runbooks. Every answer is drawn
                   from your own documents and shows its sources — nothing invented.</p>
                <div class="nv-rails">
                    <span class="nv-rail"><b>Closed</b> corpus</span>
                    <span class="nv-rail"><b>Cited</b> or silent</span>
                    <span class="nv-rail"><b>Permission</b>-aware</span>
                    <span class="nv-rail">Stays in your <b>boundary</b></span>
                </div>
            </div>
            <hr class="nv-sep"/>
            """,
            unsafe_allow_html=True,
        )

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    def sidebar(self):
        with st.sidebar:
            st.markdown("### Access profile")
            st.caption("Permission-aware retrieval — you only see what your role is entitled to.")
            level = st.radio(
                "Signed in as",
                options=self.config.access_levels,
                format_func=lambda x: ROLE_LABELS.get(x, x.title()),
                index=self.config.access_levels.index(st.session_state.user_level),
            )
            if level != st.session_state.user_level:
                st.session_state.user_level = level
                st.rerun()

            st.markdown("<hr class='nv-sep'/>", unsafe_allow_html=True)
            st.markdown("### Try asking")
            samples = [
                "How is tenant parking allocated?",
                "How do I log a maintenance request?",
                "What's the visitor sign-in procedure?",
                "What happens during loadshedding?",
                "How do I request after-hours air-conditioning?",
                "What are Growthpoint's green building targets?",
                "How does a green lease work?",
                "What should I do if someone is trapped in a lift?",
                "How are operating costs calculated in an office lease?",
            ]
            for q in samples:
                if st.button(q, key=f"s_{abs(hash(q))}"):
                    self._queue(q)
                    st.rerun()

            st.markdown("<hr class='nv-sep'/>", unsafe_allow_html=True)
            if st.button("Clear conversation"):
                st.session_state.messages = []
                st.session_state.feedback = {}
                st.session_state.pending = None
                st.rerun()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _bubble(role: str, text: str, silent: bool = False) -> str:
        if role == "user":
            return (
                '<div class="nv-row user">'
                '<div class="nv-av me">You</div>'
                f'<div class="nv-bubble user">{text}</div></div>'
            )
        cls = "bot silent" if silent else "bot"
        return (
            '<div class="nv-row">'
            '<div class="nv-av bot">◆</div>'
            f'<div class="nv-bubble {cls}">{text}</div></div>'
        )

    def _render_sources(self, sources):
        if not sources:
            return
        st.markdown(
            f'<div class="nv-src-head">Sources · {len(sources)} document'
            f'{"s" if len(sources) != 1 else ""}</div>',
            unsafe_allow_html=True,
        )
        parts = ['<div class="nv-srclist">']
        for i, s in enumerate(sources, 1):
            snippet = s.get_display_content(150)
            parts.append(
                f'<div class="nv-src">'
                f'<div class="nv-src-num">{i}</div>'
                f'<div class="nv-src-body">'
                f'<div class="nv-src-title">{s.title}'
                f'<span class="nv-badge {s.access_level}">{s.access_level}</span></div>'
                f'<div class="nv-src-meta">{s.doc_ref} · {s.section}</div>'
                f'<div class="nv-src-snip">{snippet}</div>'
                f'</div></div>'
            )
        parts.append("</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)

    def render_message(self, idx: int, msg: ChatMessage):
        if msg.role == MessageRole.USER:
            st.markdown(self._bubble("user", msg.content), unsafe_allow_html=True)
            return

        st.markdown(
            self._bubble("bot", msg.content, silent=not msg.answered),
            unsafe_allow_html=True,
        )
        self._render_sources(msg.sources)

        if msg.answered:
            fb = st.session_state.feedback.get(idx)
            st.markdown('<div class="nv-fb">', unsafe_allow_html=True)
            cols = st.columns([2, 7])
            with cols[0]:
                choice = st.feedback(
                    "thumbs",
                    key=f"fb_{idx}",
                    disabled=fb is not None,
                )
            st.markdown("</div>", unsafe_allow_html=True)
            if choice is not None and fb is None:
                st.session_state.feedback[idx] = "up" if choice == 1 else "down"
                st.rerun()
            if fb == "up":
                st.caption("Thanks — logged as a helpful answer.")
            elif fb == "down":
                st.caption("Logged as a knowledge gap, so the knowledge base improves over time.")

    # ------------------------------------------------------------------
    # Message flow
    # ------------------------------------------------------------------
    def _queue(self, prompt: str):
        """Record the user turn and mark it pending so the next run streams it."""
        st.session_state.messages.append(ChatMessage(role=MessageRole.USER, content=prompt))
        st.session_state.pending = prompt

    def _stream_pending(self):
        """Stream the assistant answer for the pending prompt, live in the UI."""
        prompt = st.session_state.pending
        history = [m for m in st.session_state.messages if m.role == MessageRole.USER or m.answered]

        # Render user turn (already appended) plus a streaming assistant row.
        for idx, msg in enumerate(st.session_state.messages):
            self.render_message(idx, msg)

        answer_box = st.empty()
        source_box = st.empty()
        sources, answered = [], True
        acc = ""

        try:
            with st.spinner("Searching the knowledge base…"):
                stream = self.rag.chat_stream(prompt, st.session_state.user_level, history[:-1])
                for ev in stream:
                    t = ev.get("type")
                    if t == "sources":
                        sources = ev.get("sources") or []
                        answered = ev.get("answered", True)
                    elif t == "delta":
                        acc += ev["text"]
                        answer_box.markdown(
                            self._bubble("bot", acc + " ▌", silent=not answered),
                            unsafe_allow_html=True,
                        )
                    elif t == "done":
                        answered = ev.get("answered", answered)
                        sources = ev.get("sources", sources)
                    elif t == "error":
                        acc = ev["text"]
                        answered = False
        except Exception as e:  # noqa: BLE001
            acc = f"Sorry, something went wrong: {e}"
            answered = False

        # Final render (no cursor) + sources.
        answer_box.markdown(
            self._bubble("bot", acc, silent=not answered), unsafe_allow_html=True
        )
        with source_box.container():
            if answered:
                self._render_sources(sources)

        st.session_state.messages.append(
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=acc,
                sources=sources if answered else [],
                answered=answered,
            )
        )
        st.session_state.pending = None
        st.rerun()

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------
    def main_content(self):
        if not st.session_state.messages and not st.session_state.pending:
            st.markdown(
                self._bubble(
                    "bot",
                    "Hi — I'm the Growthpoint Knowledge Assistant. Ask me about leases, "
                    "policies, SOPs or technical runbooks and I'll answer from your documents, "
                    "with sources. If there's no source, I'll say so rather than guess.",
                ),
                unsafe_allow_html=True,
            )

        if st.session_state.pending:
            # A turn is queued: stream it (this renders the full history too).
            self._stream_pending()
        else:
            for idx, msg in enumerate(st.session_state.messages):
                self.render_message(idx, msg)

        if prompt := st.chat_input("Ask about leases, policies, SOPs or runbooks…"):
            self._queue(prompt)
            st.rerun()

    def run(self):
        self.page_config()
        self.inject_css()
        self.header()
        self.sidebar()
        self.main_content()


def main():
    GrowthpointApp().run()


if __name__ == "__main__":
    main()

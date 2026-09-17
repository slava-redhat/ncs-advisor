"""NCS Advisor — versioned RAG troubleshooting chat over NCS docs + solutions.

Runs the LangGraph in-process and streams into the chat. When the graph needs
more detail it returns `pending_questions`, which we render as radio/multiselect
choices (like ndvm's sufficiency gate) and resubmit as the next message.
"""
import uuid

import streamlit as st
from langchain_core.messages import HumanMessage

from advisor.graph import build_graph
from advisor.retrieval import available_versions

st.set_page_config(page_title="NCS Advisor", page_icon=":material/support_agent:",
                   layout="centered")

ASSISTANT_AVATAR = ":material/support_agent:"
USER_AVATAR = ":material/engineering:"
SUGGESTIONS = {
    ":material/dns: Node NotReady after upgrade": "A worker node is NotReady after upgrading NCS",
    ":material/lock: SSH access failing": "SSH access is failing on my nodes",
    ":material/database: Ceph health warning": "Ceph is reporting a health warning",
    ":material/cable: Pod stuck in CrashLoopBackOff": "A pod is stuck in CrashLoopBackOff",
}

# Modern chat styling — user explicitly asked for a cool chatbox.
st.html("""
<style>
  .stMainBlockContainer { max-width: 820px; }
  [data-testid="stChatMessage"] { border-radius: 16px; padding: 0.55rem 0.9rem;
      margin-bottom: 0.35rem; }
  [data-testid="stChatMessageAvatarAssistant"] { background: #61afef22; color: #61afef; }
  [data-testid="stChatMessageAvatarUser"] { background: #98c37922; color: #98c379; }
  [data-testid="stChatInput"] textarea { font-size: 0.95rem; }
  [data-testid="stSidebarHeader"] + div h1 { font-size: 20px; }
</style>
""")

graph = build_graph()

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []      # [(role, text)]
    st.session_state.pending = None    # list of question dicts awaiting answers

CONFIG = {"configurable": {"thread_id": st.session_state.thread_id}}


def run_turn(user_text: str):
    """Stream one graph turn. Returns (answer_text, pending_questions|None)."""
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        status = st.status("Thinking…", expanded=False)
        placeholder = st.empty()
        streamed = ""
        step_label = {"router": "Understanding your issue", "retrieve": "Searching NCS docs",
                      "cve_lookup": "Checking CVE data", "synthesize": "Writing the answer",
                      "clarify": "Preparing questions", "ask_version": "Checking version"}
        try:
            for mode, data in graph.stream(
                {"messages": [HumanMessage(user_text)]}, CONFIG,
                stream_mode=["updates", "messages"],
            ):
                if mode == "updates":
                    node = next(iter(data), "")
                    status.update(label=step_label.get(node, node))
                elif mode == "messages":
                    chunk, meta = data
                    if meta.get("langgraph_node") == "synthesize" and chunk.content:
                        streamed += chunk.content
                        placeholder.markdown(streamed)
            status.update(label="Done", state="complete")
        except Exception as e:
            status.update(label="Error", state="error")
            st.error(f"{type(e).__name__}: {e}")
            st.stop()
        values = graph.get_state(CONFIG).values
        answer = values["messages"][-1].content or streamed
        placeholder.markdown(answer)
    return answer, values.get("pending_questions") or None


def render_form(questions):
    """Radio/multiselect gate. Returns a composed answer string on submit, else None."""
    with st.form("clarify", clear_on_submit=True, border=False):
        composed = {}
        for q in questions:
            opts, key, label = q.get("options") or [], q["key"], q["question"]
            if not opts:
                val = st.text_input(label, key=f"f_{key}", placeholder="Type here…")
                parts = [val] if val else []
            elif q.get("widget") == "radio":
                sel = st.radio(label, opts, key=f"f_{key}", index=None)
                parts = [sel] if sel else []
            elif q.get("multi"):
                parts = list(st.pills(label, opts, selection_mode="multi", key=f"f_{key}"))
            else:
                sel = st.pills(label, opts, key=f"f_{key}")
                parts = [sel] if sel else []
            if opts:
                other = st.text_input("Other", key=f"o_{key}", placeholder="Something else…",
                                      label_visibility="collapsed")
                if other:
                    parts.append(other)
            composed[key] = (label, ", ".join(p for p in parts if p))
        if st.form_submit_button("Submit details", icon=":material/send:", type="primary"):
            lines = [f"- {label} {val}" for label, val in composed.values() if val]
            return "Details:\n" + "\n".join(lines) if lines else "No further details."
    return None


# --- header -----------------------------------------------------------------
st.title(":material/support_agent: NCS Advisor")
st.caption("Version-aware troubleshooting for Nokia NCS — grounded in the product docs.")

with st.sidebar:
    st.subheader(":material/menu_book: Knowledge base")
    try:
        vs = available_versions()
        if vs:
            st.markdown(" ".join(f":blue-badge[NCS {v}]" for v in vs))
        else:
            st.info("No versions ingested — run `make ingest`.", icon=":material/info:")
    except Exception as e:
        st.warning(f"DB unavailable: {e}", icon=":material/error:")
    st.caption("Answers are grounded in ingested docs + solutions and cited inline.")
    if st.button("New conversation", icon=":material/add_comment:", width="stretch"):
        for k in ("thread_id", "history", "pending"):
            st.session_state.pop(k, None)
        st.rerun()

# --- render history ---------------------------------------------------------
for role, text in st.session_state.history:
    st.chat_message(role, avatar=ASSISTANT_AVATAR if role == "assistant" else USER_AVATAR
                    ).markdown(text)

# --- pending choice form (takes priority over free text) --------------------
if st.session_state.pending:
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        answer_text = render_form(st.session_state.pending)
    if answer_text:
        st.session_state.pending = None
        st.session_state.history.append(("user", answer_text))
        answer, pending = run_turn(answer_text)
        st.session_state.history.append(("assistant", answer))
        st.session_state.pending = pending
        st.rerun()

# --- input: suggestion chips (empty chat) + free text -----------------------
prompt = None
if not st.session_state.history and not st.session_state.pending:
    pick = st.pills("Try asking", list(SUGGESTIONS), label_visibility="collapsed")
    if pick:
        prompt = SUGGESTIONS[pick]

typed = st.chat_input("Describe your NCS issue (mention your version, e.g. 25.7)…",
                      submit_mode="disable")
if typed:
    prompt = typed

if prompt:
    st.session_state.history.append(("user", prompt))
    st.chat_message("user", avatar=USER_AVATAR).markdown(prompt)
    answer, pending = run_turn(prompt)
    st.session_state.history.append(("assistant", answer))
    st.session_state.pending = pending
    st.rerun()

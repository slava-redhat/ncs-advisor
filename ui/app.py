"""NCS Advisor — versioned RAG troubleshooting chat over NCS docs + solutions.

Runs the LangGraph in-process and streams into the chat. When the graph needs
more detail it returns `pending_questions`, which we render as radio/multiselect
choices (like ndvm's sufficiency gate) and resubmit as the next message.
"""
import uuid

import streamlit as st
from langchain_core.messages import HumanMessage

from advisor.graph import build_graph
from advisor.llm import analyze_images_with_copilot
from advisor.retrieval import available_versions
from advisor.tsn import build_tsn_tarball, make_tsn_id
from advisor.uploads import (
    MAX_PDF_PAGES,
    MAX_UPLOAD_COUNT,
    MAX_UPLOAD_MB,
    UploadProcessingError,
    process_uploads,
)

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
st.session_state.setdefault("history", [])
st.session_state.setdefault("pending", None)
if st.session_state.history and isinstance(st.session_state.history[0], tuple):
    st.session_state.history = [
        {"role": role, "text": text, "attachments": []}
        for role, text in st.session_state.history
    ]

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


def render_history_entry(message):
    role = message["role"]
    with st.chat_message(role, avatar=ASSISTANT_AVATAR if role == "assistant" else USER_AVATAR):
        st.markdown(message["text"])
        for attachment in message.get("attachments", []):
            filename = attachment["filename"]
            st.caption(f":material/attach_file: {filename}")
            with st.expander(f"Recognized text — {filename}"):
                st.text(attachment["text"] or "No readable text detected.")
        if message.get("image_analysis"):
            with st.expander("Copilot visual analysis"):
                st.markdown(message["image_analysis"])
        if message.get("vision_error"):
            st.warning(message["vision_error"], icon=":material/warning:")


def advisor_prompt(message):
    sections = [message["text"]]
    for attachment in message.get("attachments", []):
        extracted = attachment["text"] or "(No readable text detected.)"
        sections.append(
            f"Text extracted from {attachment['filename']}:\n{extracted}")
    if message.get("image_analysis"):
        sections.append(
            "Copilot visual observations (not verified NCS product guidance):\n"
            f"{message['image_analysis']}")
    if message.get("vision_error"):
        sections.append(
            "Copilot visual analysis was unavailable. Do not infer image content; "
            "use only the OCR text above.")
    return "\n\n".join(sections)


def export_message(message):
    sections = [f"{message['role']}: {message['text']}"]
    for attachment in message.get("attachments", []):
        sections.append(f"Attachment {attachment['filename']} OCR:\n{attachment['text']}")
    if message.get("image_analysis"):
        sections.append(f"Copilot visual analysis:\n{message['image_analysis']}")
    return "\n\n".join(sections)


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
        for k in ("thread_id", "history", "pending", "tsn"):
            st.session_state.pop(k, None)
        st.rerun()

    # --- TSN export: only once the advisor has produced an answer the user can approve
    has_answer = any(m["role"] == "assistant" for m in st.session_state.history)
    if has_answer and not st.session_state.pending:
        st.divider()
        st.caption("Happy with the solution? Package it as a ready-to-run TSN.")
        if st.button("Ready to make TSN solution", icon=":material/deployed_code:",
                     width="stretch"):
            values = graph.get_state(CONFIG).values
            solution = next((m["text"] for m in reversed(st.session_state.history)
                             if m["role"] == "assistant"), "")
            conversation = "\n\n".join(
                export_message(m) for m in st.session_state.history)
            tsn_id = make_tsn_id(values.get("version"), values.get("cve_ids") or [],
                                 values.get("issue_type") or "issue")
            with st.spinner("Building TSN bundle…"):
                st.session_state.tsn = (
                    tsn_id, build_tsn_tarball(conversation, solution, tsn_id))
        if st.session_state.get("tsn"):
            tsn_id, tarball = st.session_state.tsn
            st.download_button(f"Download {tsn_id}.tar.gz", tarball,
                               file_name=f"{tsn_id}.tar.gz", mime="application/gzip",
                               icon=":material/download:", width="stretch")

# --- render history ---------------------------------------------------------
for message in st.session_state.history:
    render_history_entry(message)

# --- pending choice form (takes priority over free text) --------------------
if st.session_state.pending:
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        answer_text = render_form(st.session_state.pending)
    if answer_text:
        st.session_state.pending = None
        st.session_state.history.append({
            "role": "user", "text": answer_text, "attachments": []})
        answer, pending = run_turn(answer_text)
        st.session_state.history.append({"role": "assistant", "text": answer})
        st.session_state.pending = pending
        st.rerun()

# --- input: suggestion chips (empty chat) + free text -----------------------
submission = None
if not st.session_state.history and not st.session_state.pending:
    pick = st.pills("Try asking", list(SUGGESTIONS), label_visibility="collapsed")
    if pick:
        submission = (SUGGESTIONS[pick], [])

# ponytail: hide free-text input while a choice form is pending
if not st.session_state.pending:
    st.caption(
        "Attach a screenshot of the error or diagram, or the relevant PDF pages. "
        "Include the NCS version, affected node or role, expected vs. actual result, "
        "and exact error. "
        f"Up to {MAX_UPLOAD_COUNT} PNG/JPG/PDF files ({MAX_UPLOAD_MB} MB each; "
        f"PDFs up to {MAX_PDF_PAGES} pages). "
        "Redact secrets: extracted text is sent to the configured AI provider, "
        "and image uploads are sent to Copilot for visual analysis.")
    typed = st.chat_input(
        "Describe the issue or attach relevant evidence…",
        accept_file="multiple",
        file_type=["png", "jpg", "jpeg", "pdf"],
        max_upload_size=MAX_UPLOAD_MB,
        submit_mode="disable",
    )
    if typed is not None:
        text = typed.text if hasattr(typed, "text") else typed
        files = typed.files if hasattr(typed, "files") else []
        if text or files:
            submission = (text, files)

if submission:
    text, files = submission
    prompt = text.strip() or "Please read and interpret the attached file(s)."
    with st.status("Reading attachments…", expanded=False) as upload_status:
        try:
            uploads = process_uploads([(file.name, file.getvalue()) for file in files])
        except UploadProcessingError as exc:
            st.error(f"Could not process upload: {exc}", icon=":material/error:")
            st.stop()

        image_inputs = [
            upload.image_attachment for upload in uploads if upload.image_attachment
        ]
        image_analysis = None
        vision_error = None
        if image_inputs:
            upload_status.update(label="Analyzing images with Copilot…")
            try:
                image_analysis = analyze_images_with_copilot(prompt, image_inputs)
            except (RuntimeError, OSError, ValueError) as exc:
                vision_error = (
                    f"Copilot visual analysis is unavailable ({type(exc).__name__}); "
                    "local OCR text will still be used. Choose a vision-capable "
                    "NCS_COPILOT_MODEL or retry if diagrams matter.")
        upload_status.update(label="Attachments ready", state="complete")

    entry = {
        "role": "user",
        "text": prompt,
        "attachments": [
            {"filename": upload.filename, "text": upload.text} for upload in uploads
        ],
        "image_analysis": image_analysis,
        "vision_error": vision_error,
    }
    st.session_state.history.append(entry)
    render_history_entry(entry)

    answer, pending = run_turn(advisor_prompt(entry))
    st.session_state.history.append({"role": "assistant", "text": answer})
    st.session_state.pending = pending
    st.rerun()

"""Graph nodes.

router → ask_version            (no version stated)
       → clarify                (version known but request too vague — investigate first)
       → retrieve → cve_lookup? → synthesize
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .cve import fetch_cve, search_cve
from .llm import get_llm
from .models import RouterDecision
from .ncs_context import NCS_BRIEF
from .retrieval import available_versions, rag_search_hybrid


def _last_user(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


def router(state: dict) -> dict:
    """Resolve version, classify, extract env facts, and judge sufficiency."""
    versions = available_versions()
    already = state.get("clarified", False)
    sys = SystemMessage(content=(
        f"{NCS_BRIEF}\n\n"
        "You triage Nokia NCS support questions. Read the WHOLE conversation and extract "
        "the fields precisely, using NCS's own vocabulary (node roles, deployment flavor, "
        "lifecycle phase, subsystem). Only set `version` if the user actually stated one. "
        f"Versions in the knowledge base: {versions or 'none ingested yet'}.\n"
        "Judge `enough_context` honestly: a bare topic like 'OpenSSH issue' is NOT enough — "
        "you need the affected node role, the deployment flavor, when it happened, and the "
        "actual error/behaviour. If it's not enough, write 2-4 targeted NCS-aware questions."
        + (" NOTE: you have already asked the user once, so unless the request is still "
           "completely empty, set enough_context=True and proceed with what you have."
           if already else "")))
    d = get_llm().with_structured_output(RouterDecision).invoke([sys, *state["messages"]])
    version = d.version if d.version in versions else None
    return {"version": version, "issue_type": d.issue_type,
            "symptoms": d.symptoms or _last_user(state["messages"]),
            "cve_ids": d.cve_ids, "node_roles": d.node_roles,
            "deployment": d.deployment, "phase": d.phase,
            "enough_context": bool(d.enough_context) or already,
            "clarifying_questions": [q.model_dump() for q in d.clarifying_questions],
            "pending_questions": []}  # cleared on every routing turn


def ask_version(state: dict) -> dict:
    versions = available_versions()
    opts = ", ".join(versions) if versions else "(none ingested yet)"
    return {"messages": [AIMessage(content=(
        "Which NCS version are you running? I tailor every answer to the version, and the "
        f"knowledge base currently covers: {opts}."))]}


_DEFAULT_QUESTIONS = [
    {"key": "node_role", "question": "Which node role is affected?", "multi": True,
     "options": ["controller/master", "worker", "edge", "storage", "deployer / NCS Manager",
                 "central management", "not sure"]},
    {"key": "deployment", "question": "What is the deployment flavor?", "multi": False,
     "options": ["BareMetal", "OpenStack / CBIS", "OpenStack SR-IOV", "not sure"]},
    {"key": "phase", "question": "When did it happen?", "multi": False,
     "options": ["install / deploy", "upgrade", "scale / heal", "day-2 operation", "not sure"]},
    {"key": "error", "question": "What is the exact error, alarm code, or observed behaviour?",
     "options": [], "multi": False},
]


def clarify(state: dict) -> dict:
    """Investigate before concluding: surface targeted NCS questions as a choice form (once)."""
    qs = state.get("clarifying_questions") or _DEFAULT_QUESTIONS
    return {"messages": [AIMessage(content=(
        "Before I can give you a grounded answer I need a few details about your setup:"))],
        "clarified": True, "pending_questions": qs}


def retrieve(state: dict) -> dict:
    hits = rag_search_hybrid(state["symptoms"], version=state["version"], k=8)
    return {"hits": hits}


def cve_lookup(state: dict) -> dict:
    data = [c for cid in state.get("cve_ids", []) if (c := fetch_cve(cid))]
    if not data:  # security question with no explicit CVE: search by symptoms
        data = search_cve(state["symptoms"], limit=3)
    return {"cve_data": data}


def _context(hits, cve_data) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        m = h.get("metadata", {})
        cite = f"{m.get('title', m.get('source', '?'))} (NCS {m.get('version', '?')}"
        cite += f", p.{m['page']})" if m.get("page") else ")"
        tag = "SOLUTION" if m.get("source_type") == "solution" else "DOC"
        blocks.append(f"[{i}] [{tag}] {cite}\n{h['text']}")
    for c in cve_data:
        blocks.append(f"[CVE] {c['id']} (CVSS {c.get('cvss')}, {c.get('severity')}): "
                      f"{c.get('summary', '')}\n{c.get('url', '')}")
    return "\n\n".join(blocks) if blocks else "(no matching documentation found)"


def _facts(state) -> str:
    bits = []
    if state.get("node_roles"): bits.append(f"node role(s): {', '.join(state['node_roles'])}")
    if state.get("deployment"): bits.append(f"deployment: {state['deployment']}")
    if state.get("phase"): bits.append(f"phase: {state['phase']}")
    return ("Known about the environment — " + "; ".join(bits)) if bits else ""


def synthesize(state: dict) -> dict:
    ctx = _context(state.get("hits", []), state.get("cve_data", []))
    sys = SystemMessage(content=(
        f"{NCS_BRIEF}\n\n"
        f"You are an NCS support advisor for version {state.get('version')}. "
        f"{_facts(state)}\n"
        "Answer using the SOURCES below. Structure a troubleshooting answer as: likely "
        "cause(s) → diagnostic/fix steps (commands, files, node roles) → what to check next.\n"
        "GROUNDING RULES — follow strictly:\n"
        "1. State a cause ONLY if a source's text actually ties it to the reported symptom, "
        "and cite it [n]. Do not connect topics just because they're both 'security'.\n"
        "2. Release Notes / known-issue lists are CHANGELOGS. A 'NCSFM-####' entry is a "
        "known-issue/fixed-in reference, NOT a diagnosis. Mention one only if its own "
        "description matches this symptom, and label it 'known issue', never assert it is "
        "the cause otherwise.\n"
        "3. Separate 'Directly relevant' (a source addresses this exact symptom) from "
        "'Possibly related — verify' (weaker). Be honest about confidence.\n"
        "4. If the sources don't establish a cause, say so plainly and list what to collect "
        "(exact error text, /opt/bcmt/log entries, service/pod state) — do NOT manufacture "
        "causes or cite unrelated tickets to look thorough.\n"
        "5. Never invent commands, file paths, or facts not in the sources or NCS_BRIEF.\n\n"
        f"SOURCES:\n{ctx}"))
    ans = get_llm().invoke([sys, *state["messages"]])
    return {"messages": [ans]}


# --- conditional edges ------------------------------------------------------
def route_after_router(state: dict) -> str:
    if not state.get("version"):
        return "ask_version"
    if not state.get("enough_context"):
        return "clarify"
    return "retrieve"


def route_after_retrieve(state: dict) -> str:
    return "cve_lookup" if (state.get("issue_type") == "security"
                            or state.get("cve_ids")) else "synthesize"

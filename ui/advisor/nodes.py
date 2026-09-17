"""Graph nodes.

router → ask_version            (no version stated)
       → clarify                (version known but request too vague — investigate first)
       → retrieve → cve_lookup? → synthesize
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .cve import extract_cve_ids, fetch_cve, search_cve
from .llm import get_llm
from .models import RouterDecision
from .ncs_context import NCS_BRIEF
from .retrieval import available_versions, rag_search_hybrid


def _last_user(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "")
    return ""


def _conversation_text(messages) -> str:
    parts = []
    for message in messages:
        content = message.content if hasattr(message, "content") else message.get("content", "")
        if content:
            parts.append(content)
    return "\n".join(parts)


def _normalize_deployment(raw: str | None) -> str | None:
    """Defensive normalization to canonical CN-A/CN-B labels, in case the LLM extraction
    doesn't follow the schema description verbatim (e.g. echoes 'bare-metal' from the
    user's own words). CN-B and CN-A are distinct implementations — never let 'bare metal'
    wording alone imply CN-A's Ironic-based OpenStack bare-metal host provisioning."""
    if not raw:
        return None
    low = raw.lower()
    if "sriov" in low or "sr-iov" in low:
        return "CN-A-SRIOV"
    if "cn-b" in low or "cnb" in low or "bare" in low:
        return "CN-B"
    if "cn-a" in low or "cna" in low or "openstack" in low or "cbis" in low or "virtual" in low:
        return "CN-A"
    return raw


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
    discovered_cves = extract_cve_ids(_conversation_text(state["messages"]))
    cve_ids = list(dict.fromkeys(
        [cid.upper() for cid in d.cve_ids] + discovered_cves
    ))
    issue_type = "security" if cve_ids else d.issue_type
    cve_has_minimum_context = bool(version and cve_ids)
    return {"version": version, "issue_type": issue_type,
            "symptoms": d.symptoms or _last_user(state["messages"]),
            "cve_ids": cve_ids, "node_roles": d.node_roles,
            "deployment": _normalize_deployment(d.deployment), "phase": d.phase,
            "enough_context": bool(d.enough_context) or already or cve_has_minimum_context,
            "clarifying_questions": [q.model_dump() for q in d.clarifying_questions],
            "pending_questions": []}  # cleared on every routing turn


def ask_version(state: dict) -> dict:
    versions = available_versions()
    opts = ", ".join(versions) if versions else "(none ingested yet)"
    question = (
        "Which NCS version are you running? I tailor every answer to the version, and the "
        f"knowledge base currently covers: {opts}.")
    if not versions:
        return {"messages": [AIMessage(content=question)]}
    return {"messages": [AIMessage(content=question)], "pending_questions": [
        {"key": "version", "question": question, "options": versions, "multi": False,
         "widget": "radio"}]}


_DEFAULT_QUESTIONS = [
    {"key": "node_role", "question": "Which node role is affected?", "multi": True,
     "options": ["controller/master", "worker", "edge", "storage", "deployer / NCS Manager",
                 "central management", "not sure"]},
    {"key": "deployment", "question": "What is the deployment flavor?", "multi": False,
     "options": ["CN-B (BareMetal)", "CN-A (Virtualized/OpenStack)", "CN-A SR-IOV", "not sure"]},
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
    ids = list(dict.fromkeys(
        [cid.upper() for cid in state.get("cve_ids", [])]
        + extract_cve_ids(_conversation_text(state["messages"]))
    ))
    data = [c for cid in ids if (c := fetch_cve(cid))]
    if not data:  # security question with no explicit CVE: search by symptoms
        data = search_cve(state["symptoms"], limit=3)
    if data:
        # A CVE id alone rarely appears in NCS documents. Search again using
        # vendor facts and package names so NCS's supported OS/procedure is found.
        cve_terms = " ".join(
            " ".join(filter(None, [
                c.get("summary", ""),
                c.get("red_hat_statement", ""),
                " ".join(r.get("package", "") for r in c.get("affected_releases", [])),
            ]))
            for c in data
        )
        enriched_query = f"{state['symptoms']} {cve_terms} NCS {state.get('version', '')}"
        hits = rag_search_hybrid(enriched_query, version=state.get("version"), k=8)
        baseline_hits = rag_search_hybrid(
            f"NCS {state.get('version', '')} Release Notes corrected common "
            "security vulnerabilities kernel RHEL OS baseline",
            version=state.get("version"),
            k=4,
        )
        seen = {
            (h.get("metadata", {}).get("source"), h.get("metadata", {}).get("page"))
            for h in baseline_hits
        }
        hits = baseline_hits + [
            h for h in hits
            if (h.get("metadata", {}).get("source"), h.get("metadata", {}).get("page"))
            not in seen
        ]
        hits = hits[:8]
        return {"cve_data": data, "cve_ids": ids, "hits": hits}
    return {"cve_data": data, "cve_ids": ids}


def _context(hits, cve_data) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        m = h.get("metadata", {})
        cite = f"{m.get('title', m.get('source', '?'))} (NCS {m.get('version', '?')}"
        cite += f", p.{m['page']})" if m.get("page") else ")"
        tag = "SOLUTION" if m.get("source_type") == "solution" else "DOC"
        blocks.append(f"[{i}] [{tag}] {cite}\n{h['text']}")
    for c in cve_data:
        blocks.append(
            f"[CVE/NVD] {c['id']} (CVSS {c.get('cvss')}, {c.get('severity')}): "
            f"{c.get('summary', '')}\n{c.get('url', '')}"
        )
        if c.get("red_hat_statement") or c.get("affected_releases"):
            fixed = "; ".join(
                f"{r.get('product_name')}: {r.get('package')} ({r.get('advisory')})"
                for r in c.get("affected_releases", [])
            )
            blocks.append(
                f"[CVE/Red Hat] {c['id']}: {c.get('red_hat_statement', '')}\n"
                f"Fixed releases: {fixed or 'not listed'}\n"
                f"{c.get('red_hat_url', '')}"
            )
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
        "5. Never invent commands, file paths, or facts not in the sources or NCS_BRIEF.\n"
        "6. CN-B (BareMetal) and CN-A (Virtualized, runs on Nokia CBIS or vanilla "
        "OpenStack) are DISTINCT implementations. If the environment's deployment is "
        "CN-B, you MUST ignore/exclude any source whose content is about OpenStack "
        "tooling (`openstack ...` CLI, Ironic, Ironic states like wait-call-back/deploy-"
        "failed, CBIS, Deployer VM, OpenTofu/CLCM) — that is CN-A's infrastructure layer, "
        "never CN-B's, even if the source also uses the words 'bare metal' (CN-A's own "
        "compute hosts are physical too). BMC/IPMI/BIOS-boot-order troubleshooting IS "
        "valid for CN-B — just via NCS Manager/direct BMC, never via `openstack "
        "baremetal`/Ironic. Symmetrically, if the deployment is CN-A, do not apply NCS-"
        "Manager-only (CN-B) procedures. If no source matches the stated flavor, say so "
        "and ask for flavor-specific documentation rather than borrowing the other "
        "flavor's steps.\n"
        "7. NODE-ROLE PRECISION: the user told you which node role(s) are affected "
        "(controller/master, worker, edge, storage, deployer, etc.) — never present a "
        "source about a DIFFERENT node role as a 'Directly relevant' cause, and never give "
        "it a prescriptive fix action, just because the surface-level condition looks "
        "similar (e.g. a controller-node 'SchedulingDisabled'/NotReady fix is NOT "
        "automatically the cause of a reported WORKER-node NotReady — do not tell the user "
        "to go uncordon a controller unless they told you a controller is involved). If a "
        "source only covers a different role, do NOT put it in 'Likely Cause(s)' at all — "
        "move it to a 'Things to verify' / triage question instead, phrased as a question "
        "(e.g. 'Are all controller/master nodes Ready and not SchedulingDisabled? A "
        "different-role source [n] notes this can affect scheduling — worth ruling out, "
        "but it's not confirmed relevant to your worker-role symptom'). Only call something "
        "a cause once a source explicitly ties it to the SAME role the user reported.\n"
        "8. CVE REMEDIATION: for a CVE request, do not stop at 'not listed in NCS "
        "Release Notes' or tell the user only to contact support. Use the external CVE "
        "record to state the affected component, attack impact, and vendor fixed package "
        "or advisory when available. Use NCS sources to identify the release's OS baseline, "
        "deployment flavor, supported upgrade/kernel procedure, and operational impact. "
        "Then give a concrete, conditional fix plan: first collect the running kernel and "
        "package version, compare it to the vendor fixed build, update through the "
        "supported NCS/RHEL repository or NCS maintenance path, reboot/roll nodes in the "
        "documented safe order, and verify cluster health. Never claim a package is "
        "NCS-certified when the sources do not establish that; distinguish 'vendor fix "
        "exists' from 'Nokia has certified this fix for this NCS release'. If the exact "
        "running package is unknown, give the commands to collect it and state the "
        "decision boundary instead of refusing to help. Never present the NCS release "
        "baseline as the user's observed kernel. Never assume controller count, HA "
        "topology, quorum, downtime, or that a controller is the affected node. Do not "
        "prescribe raw yum/dnf/reboot or repository-enablement commands as guaranteed "
        "NCS procedures unless a source explicitly documents them; if giving a generic "
        "RHEL command, label it as conditional, require a supported repository, and "
        "warn that Nokia compatibility must be verified before applying it.\n\n"
        "9. SOURCE AUTHORITY FOR OS BASELINES: when sources disagree about the OS or "
        "kernel, prefer the version's Release Notes 'Corrected common security "
        "vulnerabilities' table and the version-specific installation/upgrade guide. "
        "Do not treat an old SATE, Networking Guide, troubleshooting example, or sample "
        "cluster output as the current NCS baseline. Such examples may describe an older "
        "deployment and must be labeled as examples, not applied to the user's cluster. "
        "Never convert a kernel shown in documentation into 'your running kernel' unless "
        "the user supplied that output. The absence of a CVE from NCS Release Notes means "
        "only that the release-note table does not document it; it does NOT prove that "
        "the installed kernel is vulnerable or that the release ships a vulnerable "
        "kernel. Establish applicability only by comparing the user's observed package "
        "to the vendor's affected/fixed ranges.\n\n"
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

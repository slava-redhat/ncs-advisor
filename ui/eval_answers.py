"""Answer-grounding eval: run the real graph end-to-end and assert on the answer.

`eval_rag.py` measures *retrieval* (is the right source in top-k). This measures
*synthesis* — where the grounding guardrails live (advisor/nodes.py) and where a
model or prompt change will silently regress. It checks, per case:

  - require_citation : the answer carries at least one valid [n] citation and NO
                       neutralized [?] marker (the citation verifier fired clean);
  - must_include     : every listed term appears (answer stayed on the symptom);
  - must_exclude     : no listed term appears (e.g. a CN-B answer must not
                       prescribe CN-A's `openstack baremetal` tooling).

Run inside the UI container, where PostgreSQL, Ollama, and Vertex are reachable:

    python eval_answers.py            # exits non-zero if any case fails

Cases must state a version and enough concrete detail to route straight to
synthesis; a case that lands in ask_version/clarify (no answer produced) fails
loudly so the case gets fixed.
"""
import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage

from advisor.graph import build_graph

DEFAULT_CASES = Path(__file__).with_name("eval_answers.json")
_CITE_RE = re.compile(r"\[(\d+)\]")


def run_case(graph, case: dict) -> dict:
    """Invoke the graph on one case and return the final answer + retrieval state."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = graph.invoke({"messages": [HumanMessage(case["query"])]}, config)
    answer = state["messages"][-1].content if state.get("messages") else ""
    return {
        "answer": answer,
        "n_hits": len(state.get("hits", [])),
        "reached_synthesis": not state.get("pending_questions"),
    }


def check(case: dict, result: dict) -> list[str]:
    """Return a list of failure strings; empty means the case passed."""
    answer, low = result["answer"], (result["answer"] or "").lower()
    fails = []
    if not result["reached_synthesis"]:
        return ["did not reach synthesis (routed to ask_version/clarify) — "
                "make the query state a version + concrete symptom"]
    for term in case.get("must_include", []):
        if term.lower() not in low:
            fails.append(f"missing required term: {term!r}")
    for term in case.get("must_exclude", []):
        if term.lower() in low:
            fails.append(f"contains forbidden term: {term!r}")
    if case.get("require_citation"):
        cites = [int(n) for n in _CITE_RE.findall(answer)]
        valid = [n for n in cites if 1 <= n <= result["n_hits"]]
        if "[?]" in answer:
            fails.append("answer has a neutralized [?] citation (verifier fired)")
        if not valid:
            fails.append(f"no valid [n] citation (found {cites}, {result['n_hits']} sources)")
    return fails


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text())
    graph = build_graph()

    failed = 0
    for case in cases:
        try:
            result = run_case(graph, case)
            fails = check(case, result)
        except Exception as e:  # a crash is a failure, not a stack trace to lose
            fails = [f"{type(e).__name__}: {e}"]
        if fails:
            failed += 1
            print(f"FAIL {case['id']}")
            for f in fails:
                print(f"     - {f}")
        else:
            print(f"PASS {case['id']}")
    print(f"\n{len(cases) - failed}/{len(cases)} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

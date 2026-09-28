# 4. Grounding lives in the synthesize prompt

- **Status:** Accepted (with known gaps)
- **Date:** 2026-09-28

## Context

The failure mode that matters for a support advisor is a *confident wrong
answer*: connecting unrelated "security" topics, presenting a changelog
`NCSFM-####` entry as a diagnosis, applying a CN-A (OpenStack/Ironic) procedure
to a CN-B (BareMetal) cluster, or telling someone to uncordon a controller when
they reported a *worker* problem. These are domain traps, not generic
hallucinations.

## Decision

Encode grounding as **explicit rules in the `synthesize` system prompt**
(`advisor/nodes.py`), backed by a shared domain brief (`advisor/ncs_context.py`,
`NCS_BRIEF`) injected into both `router` and `synthesize`. The rules currently
enforce, among others:

- cite `[n]` only when a source *ties the cause to the reported symptom*;
- treat Release-Notes/known-issue lists as changelogs, not diagnoses;
- separate "Directly relevant" from "Possibly related — verify";
- **CN-A vs CN-B isolation** — never borrow the other flavor's tooling, even when
  both say "bare metal";
- **node-role precision** — don't prescribe a different role's fix;
- **CVE remediation** — distinguish "vendor fix exists" from "Nokia certified it",
  never present a doc's kernel as the user's running kernel.

Structured output (`RouterDecision`, Pydantic) guards the *input* side: the
router extracts version/flavor/role/phase into typed fields, and
`_normalize_deployment()` defensively re-canonicalizes the flavor label in code
in case the LLM echoes the user's wording.

## Consequences

- The domain traps are addressed where the model actually decides — in the
  prompt — and the rules read as an auditable checklist.
- **The synthesize prompt is ~70 lines of dense rules sent on every call.** It is
  brittle (one model update can shift adherence), expensive (tokens per turn),
  and unverified (nothing *checks* the output obeyed the rules).
- **No programmatic output guardrail.** A fabricated `[7]` citation, an answer
  built on empty retrieval, or a leaked CN-A step will not be caught by anything
  but the prompt. This is the biggest robustness gap — see
  [IMPROVEMENTS](../IMPROVEMENTS.md) for the citation-verifier and
  retrieval-grading nodes that would move the highest-value rules from "asked" to
  "enforced".
- Some rules (CN-A/CN-B, node-role) could move *upstream* into retrieval-time
  metadata filters, shrinking the prompt and making the guarantee structural
  rather than persuasive. Tracked in [ADR-0006](0006-tsn-solutions-as-structured-knowledge.md)
  and IMPROVEMENTS.
</content>

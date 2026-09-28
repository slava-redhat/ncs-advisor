# 5. Claude on Vertex via a param-filtering ChatAnthropic subclass

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

The reasoning LLM is corporate Claude served through Vertex AI. In this
environment the corporate `anthropic` Vertex SDK exposes a **trimmed
`Messages.create()` signature** — it does not accept `temperature`, `top_k`, or
`top_p`. But `langchain-google-vertexai`'s `ChatAnthropicVertex` always injects
those sampling params, so a vanilla call raises `TypeError`.

## Decision

Subclass it (`advisor/llm.py`, `_NCSChat`) and override `_format_params` to
**intersect the outgoing params with what this client's `create()` actually
accepts** — computed once from `inspect.signature(Messages.create)`. Unsupported
knobs are dropped instead of raising. The client is an `@lru_cache` singleton;
region defaults to `us-east5` (Anthropic-on-Vertex needs a real region, not
`global`); model defaults to `claude-sonnet-4-5@20250929`, all env-overridable.

## Consequences

- Works with the corporate SDK without forking LangChain or pinning versions.
- The filter is **signature-driven**, so if the SDK later restores those params
  they flow through automatically — no code change. Robust to the quirk changing.
- Sampling is therefore *not tunable* here (no temperature control); acceptable
  for a grounded, low-creativity support task.
- `max_tokens=4096`. Long multi-role troubleshooting answers can hit that ceiling
  and truncate — raise it or stream-and-continue if truncation shows up.
- Region/model are the two most likely things to change per deployment; both are
  env vars (`NCS_VERTEX_LOCATION`, `NCS_LLM_MODEL`).
</content>

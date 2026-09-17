"""Reasoning LLM = corporate Claude via Vertex (langchain-google-vertexai).

The corporate `anthropic` Vertex SDK in this environment has a trimmed
Messages.create() signature (no temperature/top_k/top_p), but
langchain-google-vertexai always injects those. _NCSChat filters the outgoing
params down to what this client's create() actually accepts, so unsupported
sampling knobs don't raise TypeError.
"""
import inspect
import os
from functools import lru_cache
from typing import Any

from anthropic.resources.messages import Messages
from langchain_google_vertexai.model_garden import ChatAnthropicVertex

_ALLOWED = set(inspect.signature(Messages.create).parameters) - {"self"}

_PROJECT = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID") or os.environ.get("VERTEXAI_PROJECT")
# Anthropic-on-Vertex needs a real region (not "global"). us-east5 serves Claude.
_LOCATION = os.environ.get("NCS_VERTEX_LOCATION", "us-east5")
_MODEL = os.environ.get("NCS_LLM_MODEL", "claude-sonnet-4-5@20250929")


class _NCSChat(ChatAnthropicVertex):
    def _format_params(self, **kwargs: Any) -> dict[str, Any]:
        params = super()._format_params(**kwargs)
        return {k: v for k, v in params.items() if k in _ALLOWED}


@lru_cache(maxsize=1)
def get_llm(max_tokens: int = 4096) -> _NCSChat:
    if not _PROJECT:
        raise RuntimeError("Set ANTHROPIC_VERTEX_PROJECT_ID for Claude on Vertex")
    return _NCSChat(project=_PROJECT, location=_LOCATION, model_name=_MODEL,
                    max_tokens=max_tokens)

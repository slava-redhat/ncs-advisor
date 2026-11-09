"""Reasoning LLM selection: Claude on Vertex or GitHub Copilot.

The corporate `anthropic` Vertex SDK in this environment has a trimmed
Messages.create() signature (no temperature/top_k/top_p), but
langchain-google-vertexai always injects those. _NCSChat filters the outgoing
params down to what this client's create() actually accepts, so unsupported
sampling knobs don't raise TypeError.

When Vertex project configuration is absent, Copilot is selected automatically.
"""
import asyncio
import base64
import inspect
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Sequence

from anthropic.resources.messages import Messages
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_google_vertexai.model_garden import ChatAnthropicVertex
from pydantic import BaseModel

_ALLOWED = set(inspect.signature(Messages.create).parameters) - {"self"}


def _selected_provider() -> str:
    provider = os.environ.get("NCS_LLM_PROVIDER", "").strip().lower()
    if provider:
        if provider not in {"vertex", "copilot"}:
            raise RuntimeError("NCS_LLM_PROVIDER must be 'vertex' or 'copilot'")
        return provider
    project = (os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
               or os.environ.get("VERTEXAI_PROJECT"))
    return "vertex" if project else "copilot"


class _NCSChat(ChatAnthropicVertex):
    def _format_params(self, **kwargs: Any) -> dict[str, Any]:
        params = super()._format_params(**kwargs)
        return {k: v for k, v in params.items() if k in _ALLOWED}


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"] for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return str(content)


def _format_messages(messages: Sequence[BaseMessage]) -> str:
    roles = {"system": "System", "human": "User", "ai": "Assistant"}
    return "\n\n".join(
        f"{roles.get(message.type, message.type.title())}:\n{_message_text(message.content)}"
        for message in messages
    )


async def _copilot_complete(
    prompt: str, model: str, attachments: list[dict[str, str]] | None = None,
) -> str:
    from copilot import CopilotClient
    from copilot.session_events import AssistantMessageData

    async with CopilotClient(
        mode="empty",
        working_directory="/tmp",
        base_directory="/tmp/ncs-advisor-copilot",
    ) as client:
        async with await client.create_session(
            model=model,
            available_tools=[],
            streaming=False,
        ) as session:
            response = await session.send_and_wait(prompt, attachments=attachments)

    if response is None or not isinstance(response.data, AssistantMessageData):
        raise RuntimeError("Copilot returned no assistant response")
    if not response.data.content:
        raise RuntimeError("Copilot returned an empty assistant response")
    return response.data.content


def _copilot_complete_sync(
    prompt: str, model: str, attachments: list[dict[str, str]] | None = None,
) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_copilot_complete(prompt, model, attachments))
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            asyncio.run, _copilot_complete(prompt, model, attachments)
        ).result()


def analyze_images_with_copilot(
    question: str, images: Sequence[tuple[str, str, bytes]],
) -> str:
    """Ask Copilot Vision for observations about the uploaded images."""
    if _selected_provider() != "copilot":
        raise RuntimeError("Image analysis requires NCS_LLM_PROVIDER=copilot")
    if not os.environ.get("COPILOT_GITHUB_TOKEN"):
        raise RuntimeError(
            "Copilot image analysis requires COPILOT_GITHUB_TOKEN in the UI container")
    if not images:
        raise ValueError("At least one image is required")

    attachments = [
        {
            "type": "blob",
            "data": base64.b64encode(data).decode("ascii"),
            "mimeType": mime_type,
            "displayName": filename,
        }
        for filename, mime_type, data in images
    ]
    prompt = (
        "Analyze the attached image(s) for this NCS support question: "
        f"{question}\n\n"
        "For screenshots, transcribe relevant visible text. For diagrams, describe "
        "the visible components and relationships. Separate direct observations from "
        "interpretation, and say when details are illegible. Do not invent NCS product "
        "facts or troubleshooting guidance. Keep the report concise."
    )
    from copilot._jsonrpc import JsonRpcError, ProcessExitedError

    try:
        return _copilot_complete_sync(
            prompt, os.environ.get("NCS_COPILOT_MODEL", "auto"), attachments)
    except (JsonRpcError, ProcessExitedError) as exc:
        raise RuntimeError(
            f"Copilot image analysis failed ({type(exc).__name__})") from exc


class _CopilotChat(BaseChatModel):
    model_name: str = "auto"
    max_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "github-copilot"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "max_tokens": self.max_tokens}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        content = _copilot_complete_sync(_format_messages(messages), self.model_name)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs: Any):
        if include_raw:
            raise NotImplementedError("Copilot structured output does not support include_raw")
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("Copilot structured output requires a Pydantic model class")
        parser = PydanticOutputParser(pydantic_object=schema)

        def parse(messages: Sequence[BaseMessage]):
            request = [
                SystemMessage(content=(
                    "Return only a JSON object matching this schema. Do not include "
                    "markdown fences or explanatory text.\n"
                    f"{parser.get_format_instructions()}")),
                *messages,
            ]
            response = self.invoke(request)
            return parser.parse(response.content)

        return RunnableLambda(parse)

@lru_cache(maxsize=1)
def get_llm(max_tokens: int = 4096) -> BaseChatModel:
    if _selected_provider() == "vertex":
        project = (os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                   or os.environ.get("VERTEXAI_PROJECT"))
        if not project:
            raise RuntimeError("Set ANTHROPIC_VERTEX_PROJECT_ID for Claude on Vertex")
        # Anthropic-on-Vertex needs a real region (not "global"). us-east5 serves Claude.
        location = os.environ.get("NCS_VERTEX_LOCATION", "us-east5")
        model = os.environ.get("NCS_LLM_MODEL", "claude-sonnet-4-5@20250929")
        return _NCSChat(project=project, location=location, model_name=model,
                        max_tokens=max_tokens)

    if not os.environ.get("COPILOT_GITHUB_TOKEN"):
        raise RuntimeError(
            "Copilot is selected but no token is available. Set COPILOT_GITHUB_TOKEN "
            "in the host environment; podman-compose forwards it to the UI container.")
    return _CopilotChat(
        model_name=os.environ.get("NCS_COPILOT_MODEL", "auto"),
        max_tokens=max_tokens,
    )

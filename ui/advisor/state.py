"""Graph state. Conversation is checkpointed per Streamlit session (thread_id)."""
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


class AdvisorState(TypedDict):
    messages: Annotated[list, add_messages]
    version: str | None
    issue_type: str
    symptoms: str
    cve_ids: list[str]
    node_roles: list[str]
    deployment: str | None
    phase: str | None
    clarified: bool          # True once we've asked clarifying questions (ask at most once)
    enough_context: bool
    clarifying_questions: list[dict]   # [{key, question, options, multi}]
    pending_questions: list[dict]      # set when the UI should render a choice form
    hits: list[dict]
    cve_data: list[dict]

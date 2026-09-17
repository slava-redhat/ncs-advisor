"""Structured outputs for the graph nodes."""
from typing import Literal
from pydantic import BaseModel, Field


class ClarifyQuestion(BaseModel):
    """One targeted triage question, rendered as radio/multiselect in the UI."""
    key: str = Field(..., description="short slug, e.g. node_role, deployment, phase, error")
    question: str = Field(..., description="the question shown to the user")
    options: list[str] = Field(
        default_factory=list, description="closed choices (NCS-specific). Leave EMPTY for a "
        "free-text answer like an exact error message.")
    multi: bool = Field(False, description="true if several options can apply at once")


class RouterDecision(BaseModel):
    """The router's read of the request PLUS a sufficiency judgement.

    It extracts what the user has already told us about their NCS environment and
    decides whether that is enough to give a grounded answer, or whether we must
    investigate first with targeted, NCS-aware questions.
    """
    version: str | None = Field(
        None, description="NCS version, e.g. '25.7' or '25.11'. Null if not stated anywhere.")
    issue_type: Literal["howto", "troubleshoot", "security"] = Field(
        "troubleshoot", description="howto=doc/how-to question; troubleshoot=an error/alarm/"
        "failure to fix; security=CVE/vulnerability/hardening/auth question.")
    symptoms: str = Field(
        ..., description="Concise retrieval query: error text, alarm codes, component/"
        "subsystem names, exact tokens. Used for document search.")
    cve_ids: list[str] = Field(
        default_factory=list, description="Any CVE identifiers mentioned, e.g. CVE-2024-1234.")

    # --- environment facts the user has already provided (null/empty if unknown) ---
    node_roles: list[str] = Field(
        default_factory=list, description="Affected NCS node roles the user named "
        "(e.g. deployer, controller, manager, worker, storage, edge).")
    deployment: str | None = Field(
        None, description="Deployment flavor if stated: bare-metal | openstack | sriov-vm.")
    phase: str | None = Field(
        None, description="Lifecycle phase when it happened if stated: "
        "install | upgrade | operation.")

    # --- sufficiency gate ---
    enough_context: bool = Field(
        ..., description="True only if there is enough concrete detail (subsystem + a real "
        "symptom/error + roughly where it happens) to give a grounded, specific answer. "
        "False if the request is vague and you would otherwise have to guess.")
    clarifying_questions: list[ClarifyQuestion] = Field(
        default_factory=list, description="If enough_context is False, 2-4 targeted, "
        "NCS-aware questions that would let you diagnose (node role, deployment flavor, "
        "lifecycle phase, exact error text). Give closed `options` for choice questions "
        "and leave options empty for free-text ones. Empty list when enough_context is True.")

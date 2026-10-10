"""Shared tool prompt surface for local execution and platform export."""

from builder.schema.agent import AgentSpec
from builder.schema.tool import RiskClass, ToolSpec


def tool_description(t: ToolSpec, spec: AgentSpec) -> str:
    desc = spec.tool_description_overrides.get(t.name, t.description).strip()
    notes = []
    if t.risk_class is RiskClass.IRREVERSIBLE_WRITE:
        notes.append("IRREVERSIBLE action")
    elif t.is_write:
        notes.append("Changes customer data")
    if t.requires_confirmation:
        notes.append(
            "call ONLY after summarizing this exact action and all its arguments and receiving "
            "explicit yes from the customer in their latest message; changed arguments require "
            "new confirmation. To bind consent, include a line '<tool_name>: <JSON arguments>' "
            "followed by 'Do you confirm this exact action?' in the confirmation question"
        )
    if t.idempotent is not True and t.is_write:
        notes.append("never repeat after success; if the outcome is unclear, check status first")
    if t.arg_format_rules:
        notes.append(
            "argument formats — " + "; ".join(f"{k}: {v}" for k, v in t.arg_format_rules.items())
        )
    return desc + (" [" + " | ".join(notes) + "]" if notes else "")

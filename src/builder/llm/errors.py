from __future__ import annotations


class LLMError(RuntimeError):
    """Provider or transport failure."""


class BudgetExceeded(RuntimeError):
    """A hard cost/call budget was hit; callers must stop and fall back."""


class StructuredOutputError(LLMError):
    """The model did not return output matching the requested schema."""

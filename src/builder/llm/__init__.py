"""LLM access layer: roles, provider-neutral types, cache, cost cap."""

from __future__ import annotations

from builder.llm.cache import DiskCache
from builder.llm.client import LLMClient, Provider
from builder.llm.errors import BudgetExceeded, LLMError, StructuredOutputError
from builder.llm.ledger import CostLedger
from builder.llm.roles import Role
from builder.llm.types import LLMRequest, LLMResponse, Message, ToolCall, ToolDef, Usage

__all__ = [
    "BudgetExceeded",
    "CostLedger",
    "DiskCache",
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "Provider",
    "Role",
    "StructuredOutputError",
    "ToolCall",
    "ToolDef",
    "Usage",
    "build_client",
]


def build_client(config: object | None = None, provider: Provider | None = None) -> LLMClient:
    """Build the client from config; uses OpenRouter unless a provider is given."""
    from builder.config import BuilderConfig, load_config
    from builder.llm.openrouter import OpenRouterProvider

    cfg = config if isinstance(config, BuilderConfig) else load_config()
    if provider is None:
        if not cfg.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set (use FakeProvider for offline runs)")
        provider = OpenRouterProvider(
            cfg.openrouter_api_key, cfg.openrouter_base_url, cfg.llm_timeout_s
        )
    models = {role: cfg.models.for_role(role) for role in Role}
    return LLMClient(
        provider=provider,
        models=models,
        ledger=CostLedger(cfg.ledger_path, cfg.max_cost_usd),
        cache=DiskCache(cfg.cache_dir),
    )

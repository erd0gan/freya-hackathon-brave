"""Adapter selection for the CLI/pipeline."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from builder.adapters.base import PlatformAdapter
from builder.schema.bundle import DomainSpec, InputBundle

AdapterFactory = Callable[[DomainSpec], PlatformAdapter]


def make_adapter_factory(
    name: str, bundle: InputBundle, llm: Any, cfg: Any
) -> AdapterFactory | None:
    if name == "mock":

        def mock_factory(domain: DomainSpec) -> PlatformAdapter:
            from builder.adapters.mock.adapter import MockAdapter
            from builder.adapters.mock.domains import registry
            from builder.adapters.mock.llm_env import make_llm_pack

            pack = registry.get(bundle.task_id or "")
            if pack is None:
                pack = make_llm_pack(bundle, domain, llm)  # LLM-simulated backend
            return MockAdapter(pack, domain.tools, llm, domain.knowledge)

        return mock_factory
    if name == "freya":
        if not cfg.freya_api_key:
            raise SystemExit("FREYA_API_KEY is not set")

        def freya_factory(domain: DomainSpec) -> PlatformAdapter:
            from builder.adapters.freya.adapter import FreyaAdapter
            from builder.adapters.freya.client import FreyaClient

            client = FreyaClient(
                cfg.freya_api_key, cfg.freya_base_url, workspace_id=cfg.freya_workspace_id
            )
            import json

            return FreyaAdapter(
                client,
                domain.tools,
                persona_llms=json.loads(cfg.freya_persona_llms) if cfg.freya_persona_llms else None,
                channel=domain.task.channel,
                language=domain.task.language if domain.task.language != "en" else None,
            )

        return freya_factory
    raise SystemExit(f"unknown adapter '{name}' (mock | freya | none)")

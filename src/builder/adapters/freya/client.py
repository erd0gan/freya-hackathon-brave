"""Freya REST client (memo §55).

Paths, parameters and body shapes follow the published OpenAPI spec
(`vendor/freya/openapi.json`, fetched 9 Oct 2026; server
`https://app.freyavoice.ai/api/v2`). Live behaviour is still VERIFY: no API key
has been used yet. Every response is wrapped as
`{"success": bool, "data": {"message": str, "result": ...}}`; `call()` unwraps it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://app.freyavoice.ai/api/v2"

ENDPOINTS: dict[str, tuple[str, str]] = {
    # name: (method, path template) — from the OpenAPI spec
    "default_config": ("GET", "/agents/defaults"),  # query: workspaceId*, language
    "create_agent": ("POST", "/agents"),  # body: name*, language, config
    "get_agent": ("GET", "/agents/{agent_id}"),  # query: workspaceId*
    "update_agent": ("PUT", "/agents/{agent_id}"),
    "create_branch": ("POST", "/branches"),
    "create_personas": ("POST", "/personas"),  # body: personas[] (batch)
    "persona_preview": ("POST", "/personas/preview"),
    "create_simulation": ("POST", "/simulations"),
    "get_simulation": ("GET", "/simulations/{simulation_id}"),
    "start_simulation": ("POST", "/simulations/{simulation_id}/start"),
    "rerun_simulation": ("POST", "/simulations/{simulation_id}/rerun"),
    "list_iterations": ("GET", "/simulations/{simulation_id}/iterations"),
    "version_persona_runs": (
        "GET",
        "/simulations/version-persona-runs",
    ),  # workspaceId*, entityVersionId*
    "get_call": ("GET", "/call/{call_id}"),  # query: workspaceId*
    "chat_message": ("POST", "/chat/message"),
    "chat_fork": ("POST", "/chat/fork"),
    "chat_end": ("POST", "/chat/end"),
    "create_unit_test": ("POST", "/unit-tests"),
    "run_all_unit_tests": ("POST", "/unit-tests/run-all"),
    "get_unit_test_run": ("GET", "/unit-tests/runs/{run_id}"),
    "list_workflows": ("GET", "/workflow"),
    "list_workflow_templates": ("GET", "/workflow/templates"),
}

RETRY_STATUS = {429, 500, 502, 503, 504}
DONE_STATUSES = {"completed", "paused"}


class FreyaError(RuntimeError):
    pass


class FreyaClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        workspace_id: str | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        endpoints: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.endpoints = {**ENDPOINTS, **(endpoints or {})}
        self.workspace_id = workspace_id
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    def call(
        self,
        name: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        **path: str,
    ) -> Any:
        """Call an endpoint and return `data.result` from the response envelope."""
        method, template = self.endpoints[name]
        url = template.format(**path)
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.request(method, url, json=body, params=params)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise FreyaError(f"{name}: transport error {exc}") from exc
            else:
                if r.status_code < 300:
                    return _unwrap(name, r.json() if r.content else None)
                if r.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise FreyaError(f"{name}: HTTP {r.status_code} {r.text[:500]}")
            time.sleep(delay)
            delay *= 2
        raise FreyaError("unreachable")

    def _ws(self, **extra: Any) -> dict[str, Any]:
        if not self.workspace_id:
            raise FreyaError("FREYA_WORKSPACE_ID is required for this endpoint")
        return {"workspaceId": self.workspace_id, **extra}

    # Convenience wrappers ------------------------------------------------------------

    def default_config(self, language: str | None = None) -> dict[str, Any]:
        params = self._ws(**({"language": language} if language else {}))
        out = self.call("default_config", params=params)
        return dict(out.get("config", {})) if isinstance(out, dict) else {}

    def create_agent(
        self, name: str, config: dict[str, Any], language: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name[:100], "config": config}
        if language:
            body["language"] = language
        out = self.call("create_agent", body)
        return dict(out.get("agent", out)) if isinstance(out, dict) else {}

    def create_personas(self, personas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(personas), 200):  # documented max 200 per request
            res = self.call("create_personas", {"personas": personas[i : i + 200]})
            out += list(res.get("personas", [])) if isinstance(res, dict) else []
        if len(out) != len(personas):
            raise FreyaError(f"created {len(out)} personas, expected {len(personas)}")
        return out

    def create_simulation(self, body: dict[str, Any]) -> dict[str, Any]:
        out = self.call("create_simulation", body)
        return dict(out.get("simulation", out)) if isinstance(out, dict) else {}

    def get_simulation(self, simulation_id: str) -> dict[str, Any]:
        out = self.call("get_simulation", simulation_id=simulation_id)
        return dict(out) if isinstance(out, dict) else {}

    def wait_simulation(
        self, simulation_id: str, poll_s: float = 5.0, timeout_s: float = 1800.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            sim = self.get_simulation(simulation_id).get("simulation", {})
            if str(sim.get("status", "")).lower() in DONE_STATUSES:
                return dict(sim)
            if time.monotonic() > deadline:
                raise FreyaError(f"simulation {simulation_id} timed out")
            time.sleep(poll_s)

    def latest_iteration(self, simulation_id: str) -> dict[str, Any]:
        out = self.call("list_iterations", simulation_id=simulation_id)
        its = list(out.get("iterations", [])) if isinstance(out, dict) else []
        if not its:
            raise FreyaError(f"simulation {simulation_id} has no iterations")
        return dict(max(its, key=lambda x: int(x.get("iterationIndex", 0))))

    def persona_runs(self, entity_version_id: str) -> list[dict[str, Any]]:
        out = self.call("version_persona_runs", params=self._ws(entityVersionId=entity_version_id))
        return list(out.get("runs", [])) if isinstance(out, dict) else []

    def get_call(self, call_id: str) -> dict[str, Any]:
        out = self.call("get_call", params=self._ws(), call_id=call_id)
        return dict(out.get("call", out)) if isinstance(out, dict) else {}

    def chat_message(
        self,
        message: str,
        *,
        agent_id: str | None = None,
        call_id: str | None = None,
        version_id: str | None = None,
        from_message_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message[:10000]}
        for k, v in (
            ("agentId", agent_id),
            ("callId", call_id),
            ("versionId", version_id),
            ("fromMessageId", from_message_id),
        ):
            if v:
                body[k] = v
        return dict(self.call("chat_message", body))

    def chat_end(self, call_id: str) -> Any:
        return self.call("chat_end", {"callId": call_id})


def _unwrap(name: str, payload: Any) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        return payload
    if payload.get("success") is False:
        msg = (payload.get("data") or {}).get("message", "")
        raise FreyaError(f"{name}: {msg}")
    data = payload["data"]
    return data.get("result", data) if isinstance(data, dict) else data

"""Cliente HTTP assíncrono para o Hermes (pesquisa e mercado).

Endpoints esperados no monorepo Hermes:
- POST /api/research/search  → busca com citações (fonte+data)
- POST /api/research/fetch   → fetch de página
- POST /api/market/analyze   → análise de mercado (precisa LLM)
- GET  /api/market/reports   → lista relatórios
- GET  /api/market/report/{id} → HTML do relatório
- GET  /health               → health check

Autenticação: Bearer token (HERMES_HTTP_TOKEN) + X-Hermes-CSRF.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class HermesResult:
    ok: bool
    data: Any = None
    error: str | None = None
    status: int | None = None


class HermesClient:
    def __init__(
        self,
        research_url: str = "http://127.0.0.1:5021",
        market_url: str = "http://127.0.0.1:5022",
        token: str = "",
        timeout_s: float = 15.0,
    ) -> None:
        self.research_url = research_url.rstrip("/")
        self.market_url = market_url.rstrip("/")
        self.token = token
        self.timeout = timeout_s
        self._headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
            self._headers["X-Hermes-CSRF"] = token

    @property
    def available(self) -> bool:
        return bool(self.research_url) or bool(self.market_url)

    async def _post(self, base_url: str, path: str, json_body: dict[str, Any]) -> HermesResult:
        if not base_url:
            return HermesResult(ok=False, error="URL não configurada", status=503)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{base_url}{path}",
                    json=json_body,
                    headers=self._headers,
                )
        except httpx.TimeoutException:
            return HermesResult(ok=False, error="timeout", status=504)
        except httpx.HTTPError as exc:
            return HermesResult(ok=False, error=f"rede: {exc}", status=502)

        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:500]}

        if resp.status_code >= 400:
            return HermesResult(
                ok=False,
                error=payload.get("detail") or payload.get("message") or f"HTTP {resp.status_code}",
                status=resp.status_code,
                data=payload,
            )
        return HermesResult(ok=True, data=payload, status=resp.status_code)

    async def _get(self, base_url: str, path: str, params: dict[str, Any]) -> HermesResult:
        if not base_url:
            return HermesResult(ok=False, error="URL não configurada", status=503)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{base_url}{path}",
                    params=params,
                    headers=self._headers,
                )
        except httpx.TimeoutException:
            return HermesResult(ok=False, error="timeout", status=504)
        except httpx.HTTPError as exc:
            return HermesResult(ok=False, error=f"rede: {exc}", status=502)

        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:500]}

        if resp.status_code >= 400:
            return HermesResult(
                ok=False,
                error=payload.get("detail") or payload.get("message") or f"HTTP {resp.status_code}",
                status=resp.status_code,
                data=payload,
            )
        return HermesResult(ok=True, data=payload, status=resp.status_code)

    # ----- Pesquisa (busca com citações, fetch) -----
    async def research(
        self,
        query: str,
        *,
        limit: int = 10,
        providers: list[str] | None = None,
        trace_id: str | None = None,
        timeout_ms: int | None = None,
        allow_public: bool | None = None,
    ) -> HermesResult:
        """Busca tendências/nichos via Hermes Research (POST /api/research/search)."""
        import uuid
        body: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "traceId": trace_id or str(uuid.uuid4()),
        }
        if providers:
            body["providers"] = providers
        if timeout_ms:
            body["timeoutMs"] = timeout_ms
        if allow_public is not None:
            body["allowPublic"] = allow_public
        return await self._post(self.research_url, "/api/research/search", body)

    async def research_fetch(
        self,
        url: str,
        *,
        max_bytes: int = 50000,
    ) -> HermesResult:
        """Fetch de página via Hermes Research (POST /api/research/fetch)."""
        return await self._post(
            self.research_url,
            "/api/research/fetch",
            {"url": url, "max_bytes": max_bytes},
        )

    # ----- Mercado (análise, relatórios) -----
    async def market_analyze(
        self,
        topic: str,
        *,
        project_id: str | None = None,
        trace_id: str | None = None,
        max_sources: int = 12,
        fetch_top: int = 3,
    ) -> HermesResult:
        """Análise de mercado via Hermes Market (POST /api/market/analyze)."""
        import uuid
        body: dict[str, Any] = {
            "topic": topic,
            "maxSources": max_sources,
            "fetchTop": fetch_top,
            "traceId": trace_id or str(uuid.uuid4()),
        }
        if project_id:
            body["projectId"] = project_id
        return await self._post(self.market_url, "/api/market/analyze", body)

    async def market_reports(self) -> HermesResult:
        """Lista relatórios de mercado (GET /api/market/reports)."""
        return await self._get(self.market_url, "/api/market/reports", {})

    async def market_report(self, report_id: str) -> HermesResult:
        """HTML de um relatório específico (GET /api/market/report/{id})."""
        return await self._get(self.market_url, f"/api/market/report/{report_id}", {})

    # ----- Health check -----
    async def health(self) -> dict[str, Any]:
        results = {}
        for name, url in (("research", self.research_url), ("market", self.market_url)):
            if not url:
                results[name] = {"ok": False, "reason": "not_configured"}
                continue
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{url}/health", headers=self._headers)
                results[name] = {"ok": resp.status_code == 200, "status": resp.status_code}
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}
        return results
"""Cliente HTTP assíncrono para o Hermes (pesquisa e mercado).

Endpoints esperados no monorepo Hermes:
- GET  /v1/research?q=...&limit=10  → tendências, SERPs, nichos
- GET  /v1/market?product_id=...    → dados de produto, preços, concorrentes

Autenticação: Bearer token (HERMES_HTTP_TOKEN) ou header X-Hermes-Token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

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
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    @property
    def available(self) -> bool:
        # Considera disponível se pelo menos uma URL configurada
        return bool(self.research_url) or bool(self.market_url)

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

    # ----- Pesquisa (tendências, SERPs, nichos) -----
    async def research(
        self,
        query: str,
        *,
        limit: int = 10,
        region: str = "BR",
        language: str = "pt-BR",
    ) -> HermesResult:
        """Busca tendências/nichos via Hermes Research.
        Espera resposta: { ok: true, results: [...], source: "serpapi|echotok|..." }
        """
        return await self._get(
            self.research_url,
            "/v1/research",
            {"q": query, "limit": limit, "region": region, "language": language},
        )

    async def research_skills(
        self,
        intent: str,
        *,
        limit: int = 5,
    ) -> HermesResult:
        """Busca skills/conhecimento especializado para um intent.
        Ex.: intent="campaign-decision" ou "compliance-tiktok"
        """
        return await self._get(
            self.research_url,
            "/v1/research/skills",
            {"intent": intent, "limit": limit},
        )

    # ----- Mercado (produtos, preços, concorrentes) -----
    async def market_product(
        self,
        product_id: str,
        *,
        region: str = "BR",
    ) -> HermesResult:
        """Dados de produto: preço, comissão, vendas, rating, concorrentes."""
        return await self._get(
            self.market_url,
            "/v1/market/product",
            {"product_id": product_id, "region": region},
        )

    async def market_competitors(
        self,
        category_id: str,
        *,
        limit: int = 10,
        region: str = "BR",
    ) -> HermesResult:
        """Top concorrentes de uma categoria."""
        return await self._get(
            self.market_url,
            "/v1/market/competitors",
            {"category_id": category_id, "limit": limit, "region": region},
        )

    async def market_trends(
        self,
        *,
        category_id: str | None = None,
        subcategory_id: str | None = None,
        days: int = 30,
        region: str = "BR",
    ) -> HermesResult:
        """Tendências de mercado (volume, GMV, crescimento)."""
        params: dict[str, Any] = {"days": days, "region": region}
        if category_id:
            params["category_id"] = category_id
        if subcategory_id:
            params["subcategory_id"] = subcategory_id
        return await self._get(self.market_url, "/v1/market/trends", params)

    # ----- Health check -----
    async def health(self) -> dict[str, Any]:
        """Verifica disponibilidade de research e market."""
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
"""Cliente da EchoTok Data API (Open API v3, autenticação Basic).

A chave fica só no `.env` do sidecar. O navegador nunca a recebe: o Hub chama
`/echotok/import` com o token do sidecar e este devolve produtos normalizados.
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from typing import Any

import httpx

from .config import Settings
from .models import EchoTokProduct


class EchoTokError(RuntimeError):
    def __init__(self, message: str, status: int = 502, code: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _basic_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def build_auth_header(settings: Settings) -> str:
    """Aceita usuário/senha, `user:pass` ou uma chave única (user + senha vazia)."""
    user = str(getattr(settings, "echotok_api_user", "") or "").strip()
    password = str(getattr(settings, "echotok_api_password", "") or "").strip()
    key = str(getattr(settings, "echotok_api_key", "") or "").strip()
    if user:
        return _basic_header(user, password)
    if not key:
        return ""
    if ":" in key:
        return _basic_header(*key.split(":", 1))
    try:
        decoded = base64.b64decode(key, validate=True).decode("utf-8")
        if ":" in decoded:
            return "Basic " + key
    except Exception:
        pass
    return _basic_header(key, "")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cover(raw: Any) -> str:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        if text.startswith("http"):
            return text
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return str(items[0].get("url") or "")
    return ""


class EchoTokClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = str(
            getattr(settings, "echotok_base_url", "https://open.echotik.live/api/v3")
        ).rstrip("/")
        self.auth_header = build_auth_header(settings)
        self.timeout = float(getattr(settings, "echotok_timeout_s", 15.0) or 15.0)
        self.region = str(getattr(settings, "echotok_region", "BR") or "BR")
        self.daily_limit = int(getattr(settings, "echotok_daily_limit", 50) or 50)
        self._l1_cache: dict[str, dict[str, str]] = {}

    @property
    def available(self) -> bool:
        return bool(self.auth_header)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.auth_header:
            raise EchoTokError(
                "EchoTok não configurada: defina ECHOTOK_API_KEY ou ECHOTOK_API_USER/PASSWORD",
                status=503,
                code="NOT_CONFIGURED",
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={
                        "Authorization": self.auth_header,
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise EchoTokError("falha de rede ao chamar a EchoTok", status=502) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise EchoTokError(
                f"EchoTok retornou HTTP {response.status_code} sem JSON",
                status=502,
            ) from exc
        if response.status_code >= 400:
            raise EchoTokError(
                f"EchoTok HTTP {response.status_code}",
                status=502,
                code=payload.get("code"),
            )
        code = payload.get("code")
        if code not in (0, "0", None):
            raise EchoTokError(
                str(payload.get("message") or "erro retornado pela EchoTok"),
                status=502,
                code=code,
            )
        return payload

    async def product_list(
        self,
        *,
        region: str | None = None,
        page_size: int = 10,
        page_num: int = 1,
        category_id: str | None = None,
        sort_field: int = 5,
        sort_type: int = 1,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "region": (region or self.region)[:2].upper(),
            "page_num": max(1, page_num),
            "page_size": max(1, min(page_size, 20)),
            "product_sort_field": max(1, min(sort_field, 7)),
            "sort_type": 1 if sort_type else 0,
        }
        if category_id:
            params["category_id"] = category_id
        payload = await self._get("/echotik/product/list", params)
        data = payload.get("data") or []
        return (data if isinstance(data, list) else []), payload.get("requestId")

    async def l1_categories(self, language: str = "en-US") -> dict[str, str]:
        if language in self._l1_cache:
            return self._l1_cache[language]
        payload = await self._get(
            "/echotik/category/l1",
            {"language": language},
        )
        out: dict[str, str] = {}
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            category_id = str(row.get("category_id") or "")
            if category_id:
                out[category_id] = str(row.get("category_name") or category_id)
        self._l1_cache[language] = out
        return out

    def normalize_products(
        self,
        raw_items: list[dict[str, Any]],
        categories: dict[str, str] | None = None,
    ) -> list[EchoTokProduct]:
        names = categories or {}
        products: list[EchoTokProduct] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            product_id = str(raw.get("product_id") or "").strip()
            if not product_id:
                continue
            commission = _num(raw.get("product_commission_rate"))
            if 0 < commission <= 1:
                commission *= 100
            rating = _num(raw.get("product_rating"))
            if rating > 5:
                rating /= 10
            category_id = str(raw.get("category_id") or "")
            products.append(
                EchoTokProduct(
                    id=product_id,
                    name=str(raw.get("product_name") or "").strip(),
                    category_id=category_id,
                    category=names.get(category_id, category_id),
                    category_l2_id=str(raw.get("category_l2_id") or ""),
                    category_l3_id=str(raw.get("category_l3_id") or ""),
                    price=_num(raw.get("spu_avg_price") or raw.get("min_price")),
                    commission_pct=max(0.0, min(commission, 100.0)),
                    sales_30d=_int(raw.get("total_sale_30d_cnt")),
                    sales_7d=_int(raw.get("total_sale_7d_cnt")),
                    sales_total=_int(raw.get("total_sale_cnt")),
                    gmv_30d=_num(raw.get("total_sale_gmv_30d_amt")),
                    gmv_total=_num(raw.get("total_sale_gmv_amt")),
                    rating=max(0.0, min(rating, 5.0)),
                    review_count=_int(raw.get("review_count")),
                    trend_flag=_int(raw.get("sales_trend_flag")),
                    seller_id=str(raw.get("seller_id") or ""),
                    region=str(raw.get("region") or self.region or "BR"),
                    cover_url=_cover(raw.get("cover_url")),
                )
            )
        return products

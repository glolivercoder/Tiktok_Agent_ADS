"""Leitor das coletas Apify (Mercado Livre) para o Hub.

Fonte: os JSON brutos que scripts/apify_piloto_mercadolivre.py salva em
<data_dir>/market-reports/apify/ (regra R8: dado bruto fica fora do RAG).
Cada arquivo traz termo, seed, actor, run_id, dataset_id, datetime de coleta
e os itens do dataset com preco em BRL, nota e loja oficial.

Este modulo so NORMALIZA e agrupa; nao coleta nada (coleta = script do
operador, com as regras R1-R10 do market-intel/references/apify-rules.md).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("echotik_agent")

COLLECTION_DIR = Path("market-reports", "apify")


def _load_file(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("apify.file_invalido", extra={"evento": "apify.file_invalido", "arquivo": path.name})
        return None
    if not isinstance(doc, dict) or "termo" not in doc or not isinstance(doc.get("items"), list):
        return None
    return doc


def _item_product(it: dict, doc: dict) -> dict | None:
    price = it.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        return None  # sem preco nao presta conta (metrics-playbook: price_quality=invalido)
    title = str(it.get("title") or "").strip()
    permalink = str(it.get("permalink") or "").strip()
    if not title or not permalink:
        return None
    seller = it.get("seller") if isinstance(it.get("seller"), dict) else {}
    rating = it.get("ratingAverage")
    scraped = it.get("scrapedAt") or doc.get("started_at")
    return {
        # campos no formato do import do Hub (apiToProduct consome isso no front)
        "id": it.get("itemId") or it.get("id") or abs(hash(permalink)) % 10**9,
        "name": title,
        "price": round(float(price), 2),
        "originalPrice": it.get("originalPrice") if isinstance(it.get("originalPrice"), (int, float)) else None,
        "discountPercent": it.get("discountPercent") if isinstance(it.get("discountPercent"), (int, float)) else None,
        "rating": rating if isinstance(rating, (int, float)) else None,
        "seller": str(seller.get("storeName") or seller.get("nickname") or "sem dado"),
        "officialStore": bool(seller.get("isOfficialStore")),
        "soldQuantity": it.get("soldQuantity") if isinstance(it.get("soldQuantity"), (int, float)) else None,
        "category": doc.get("termo") or "sem termo",
        "seed": doc.get("seed") or "sem seed",
        "currency": it.get("currency") or "BRL",
        "coverUrl": it.get("thumbnailUrl"),
        "permalink": permalink,
        "source": {
            "system": "apify",
            "actor": doc.get("actor"),
            "runId": doc.get("run_id"),
            "datasetId": doc.get("dataset_id"),
            "scrapedAt": scraped,
            "region": "BR",
        },
    }


def collect(data_dir: Path) -> dict:
    dir_apify = data_dir / COLLECTION_DIR
    if not dir_apify.exists():
        return {"ok": False, "error": "APIFY_EMPTY", "message": f"diretorio ausente: {dir_apify}"}

    coletas: list[dict] = []
    products: dict[str, dict] = {}
    stale: list[str] = []

    for path in sorted(dir_apify.glob("*MLB_*.json")):
        if "resumo" in path.name:
            continue
        doc = _load_file(path)
        if doc is None:
            stale.append(path.name)
            continue
        items = [p for p in (_item_product(it, doc) for it in doc["items"]) if p]
        terms = str(doc.get("termo") or "")
        actor = str(doc.get("actor") or "")
        prices = [p["price"] for p in items]
        prices.sort()
        def pct(pr: list[float], q: float) -> float | None:
            if not pr:
                return None
            k = max(0, min(len(pr) - 1, round((len(pr) - 1) * q)))
            return round(pr[k], 2)
        coletas.append({
            "termo": terms,
            "seed": doc.get("seed"),
            "actor": actor,
            "runId": doc.get("run_id"),
            "datasetId": doc.get("dataset_id"),
            "startedAt": doc.get("started_at"),
            "bruto": path.name,
            "count": len(items),
            "semPreco": len(doc["items"]) - len(items),
            "p10": pct(prices, 0.10),
            "p50": pct(prices, 0.50),
            "p90": pct(prices, 0.90),
        })
        for p in items:
            products.setdefault(str(p["permalink"]), p)

    if not coletas:
        return {"ok": False, "error": "APIFY_EMPTY", "message": "nenhuma coleta valida"}

    catalogo = list(products.values())
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": coletas[0].get("actor"),
        "region": "BR",
        "count": len(catalogo),
        "coletas": coletas,
        "products": catalogo,
    }

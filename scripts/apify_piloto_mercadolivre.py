#!/usr/bin/env python3
"""Piloto: top buscas BR (SerpApi Google Trends) -> 20 itens ML por termo (Apify).

Fluxo (regras R3-R5 do market-intel/references/apify-rules.md):
1. SerpApi engine=google_trends_trending_searches geo=BR: termos em alta hoje.
2. Pega os N primeiros termos aptos a produto (nao-noticia), piloto maxItems=20.
3. Uma run do actor Mercado Livre por termo; poll ate SUCCEEDED.
4. Dados brutos -> data/market-reports/apify/ (fora do RAG, regra R8).
5. Resumo: preco p10/p50/p90, desconto, rating, seller oficial.

Somente stdlib.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "market-reports" / "apify"
RUN_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")


def env(key: str) -> str:
    if val := os_env(key):
        return val
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def os_env(key: str) -> str:
    import os
    return os.environ.get(key, "").strip()


def http_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def serpapi_trending(api_key: str, geo: str = "BR", n: int = 330) -> list[dict]:
    """(engine correto: google_trends_trending_now). Devolve dicts do SerpApi."""
    q = urllib.parse.urlencode({"engine": "google_trends_trending_now", "geo": geo, "hl": "pt-br", "api_key": api_key})
    data = http_json(f"https://serpapi.com/search.json?{q}")
    if "error" in data:
        raise RuntimeError(f"SerpApi: {data['error']}")
    return data.get("trending_searches", [])[:n]


# Categorias do Trends que nao sao busca de produto
CAT_NAO_PRODUTO = {
    "sports", "politics", "law and government", "news", "entertainment",
    "climate", "weather", "technology", "adult", "education", "health",
}
NEWS_WORDS = re.compile(
    r"\b(copa|sele|jogo|futebol|x\s|\d+x?|ban|stf|vota|elei|crime|pol[eí]|guerra|"
    r"ataque|candidat|salário|inflação|anos|corinthians|flamengo|palmeiras|"
    r"onde assistir|minuto a minuto|escala|classifica|morre|morte|acidente|preso)\b",
    re.IGNORECASE,
)


def product_terms(trends: list[dict], n: int) -> list[str]:
    """Termos mais proximos de busca de produto: descarta categoria de notícia,
    prioriza Shopping e afins. Mantém a ordem (o Trends já vem por volume)."""
    out = []
    for t in trends:
        q = t.get("query", "")
        cats = {c.get("name", "").lower() for c in t.get("categories", [])}
        if cats and (cats & CAT_NAO_PRODUTO):
            continue
        if NEWS_WORDS.search(q):
            continue
        out.append(q)
        if len(out) >= n:
            break
    return out


def start_run(actor_id: str, token: str, run_input: dict) -> dict:
    url = (
        f"https://api.apify.com/v2/acts/{actor_id}/runs?token={token}"
        f"&timeout=900&memoryMbytes=4096"
    )
    body = json.dumps(run_input).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["data"]


def wait_run(run_id: str, token: str, deadline_s: int = 900) -> dict:
    url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        run = http_json(url)["data"]
        if run["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return run
        time.sleep(6)
    raise TimeoutError(f"run {run_id} não terminou em {deadline_s}s")


def dataset_items(dataset_id: str, token: str, limit: int) -> list[dict]:
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}&clean=true&limit={limit}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def summarize(items: list[dict]) -> dict:
    prices = [i["price"] for i in items if isinstance(i.get("price"), (int, float)) and i["price"] > 0]
    discounts = [i["discountPercent"] for i in items if isinstance(i.get("discountPercent"), (int, float))]
    ratings = [i["ratingAverage"] for i in items if isinstance(i.get("ratingAverage"), (int, float))]
    oficiais = sum(1 for i in items if i.get("seller", {}).get("isOfficialStore"))
    sem_dado = len(items) - len(prices)
    if not prices:
        return {"itens": len(items), "sem_preco": sem_dado}
    def pct(xs, p):
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, round((len(xs) - 1) * p)))
        return xs[k]
    return {
        "itens": len(items),
        "preco_p10": pct(prices, 0.10),
        "preco_p50": pct(prices, 0.50),
        "preco_p90": pct(prices, 0.90),
        "desconto_medio_pct": round(statistics.mean(discounts), 1) if discounts else None,
        "nota_medio": round(statistics.mean(ratings), 2) if ratings else "sem dado",
        "seller_oficial": oficiais,
        "sem_preco": sem_dado,
    }


def main() -> int:
    n_terms = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    max_items = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    serp_key = env("SERPAPI_API_KEY")
    api_token = env("APIFY_API_TOKEN")
    actor = env("APIFY_ACTOR_MERCADO_LIVRE")
    if not (serp_key and api_token and actor):
        print("ERRO: precisa de SERPAPI_API_KEY, APIFY_API_TOKEN e APIFY_ACTOR_MERCADO_LIVRE no .env")
        return 2

    actor = actor.replace("/", "~", 1)

    print("[1/4] SerpApi: termos em alta no BR...")
    all_q = serpapi_trending(serp_key)
    termos = product_terms(all_q, n_terms)
    print(f"      {len(all_q)} termos -> {len(termos)} aptos a produto:")
    for t in termos:
        print(f"      - {t}")
    if not termos:
        print("nenhum termo apto hoje; nada a coletar")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    relatorio = []

    for n, termo in enumerate(termos, 1):
        tag = re.sub(r"[^a-z0-9]+", "-", termo.lower()).strip("-")[:40]
        print(f"[{n}/{len(termos)}] run piloto: '{termo}' (maxItems={max_items})...")
        run = start_run(actor, api_token, {
            "searchQueries": [termo],
            "siteId": "MLB",
            "condition": "new",
            "maxItems": max_items,
            "maxPagesPerQuery": 5,
            "maxConcurrency": 5,
        })
        print(f"      run {run['id']} iniciada ({run['status']}); aguardando...")
        try:
            run = wait_run(run["id"], api_token)
        except TimeoutError as e:
            print(f"      TIMEOUT: {e}")
            relatorio.append({"termo": termo, "erro": str(e)})
            continue
        if run["status"] != "SUCCEEDED":
            err = (run.get("statusMessage") or "")[:200]
            print(f"      FALHA ({run['status']}): {err}")
            relatorio.append({"termo": termo, "status": run["status"], "erro": err})
            continue
        items = dataset_items(run["defaultDatasetId"], api_token, max_items)
        raw_path = RAW_DIR / f"{RUN_DATE}_MLB_{tag}.json"
        raw_path.write_text(
            json.dumps(
                {
                    "termo": termo,
                    "actor": actor,
                    "run_id": run["id"],
                    "dataset_id": run["defaultDatasetId"],
                    "started_at": run["startedAt"],
                    "cost_usd_est": round(max_items * 0.002, 3),
                    "items": items,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        s = summarize(items)
        s["termo"] = termo
        s["raw"] = str(raw_path.relative_to(PROJECT_ROOT))
        relatorio.append(s)
        print(
            f"      ok: {s['itens']} itens | p50 R$ {s.get('preco_p50', 0)} "
            f"| desconto médio {s.get('desconto_medio_pct')}% | nota {s.get('nota_medio')}"
        )

    out = RAW_DIR / f"{RUN_DATE}_MLB_piloto_resumo.json"
    out.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[4/4] resumo: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

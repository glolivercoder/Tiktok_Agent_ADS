#!/usr/bin/env python3
"""Imprime tabela dos N primeiros itens de cada coleta piloto MLB do dia."""
import json, glob, sys, os
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOP = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PREFIX = sys.argv[2] if len(sys.argv) > 2 else ""
base = Path(__file__).resolve().parents[1] / "data" / "market-reports" / "apify"

def brl(v):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

print("| Termo (Trends BR hoje) | # | Produto | Preco | Desc | Nota | Loja oficial |")
print("|---|---|---|---|---|---|---|")

resumo = {}
for f in base.glob("*_piloto_resumo.json"):
    for s in json.loads(f.read_text(encoding="utf-8")):
        resumo[s["termo"]] = s

for f in sorted(base.glob("*MLB_*.json")):
    if "resumo" in f.name:
        continue
    if PREFIX and not f.name.startswith(PREFIX):
        continue
    d = json.loads(f.read_text(encoding="utf-8"))
    seed = d.get("seed", "")
    s = resumo.get(d["termo"], {})
    termo = d["termo"]
    p50 = s.get("preco_p50")
    linha = f"### {termo}" + (f" (seed: {seed})" if seed and seed != "trending" else "")
    if p50:
        linha += f" — p50 {brl(p50)} · desconto médio {s.get('desconto_medio_pct')}% · nota {s.get('nota_medio')} · {s.get('seller_oficial')}/{s['itens']} loja oficial"
    print(f"| {linha} | | | | | | |")
    for i, it in enumerate(d.get("items", [])[:TOP], 1):
        nota = it.get("ratingAverage")
        nota = f"{nota:.1f}".replace(".", ",") if isinstance(nota, (int, float)) else "sem dado"
        oficial = "✔" if (it.get("seller") or {}).get("isOfficialStore") else ""
        des = it.get("discountPercent")
        des = f"{des}%" if des else ""
        print(f"| | {i} | {(it.get('title') or '')[:46]} | {brl(it['price'])} | {des} | {nota} | {oficial} |")

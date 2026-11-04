#!/usr/bin/env python3
"""Carrega as regras da skill market-intel no RAG do sidecar.

Implementa a regra R2 de market-intel/references/apify-rules.md:
- Carrega sob demanda, o mínimo necessario (flags escolhem o escopo).
- Um manifesto registra commit e arquivos; nao recarrega o que ja esta la
  (o RAG nao deduplica nem remove).
- Atualiza so se o manifesto tiver mais de 30 dias (ou --force).

Uso:
    python scripts/load_to_rag.py              # carrega SKILL.md + references (completo)
    python scripts/load_to_rag.py --apify      # carrega so apify-rules.md (regra R1/R2)
    python scripts/load_to_rag.py --force      # ignora o manifesto e recarrega
    python scripts/load_to_rag.py --dry-run    # mostra os chunks sem enviar

Somente biblioteca padrao (urllib, json, argparse, subprocess).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Console Windows (cp1252) nao imprime setas/acentos; degrada com seguranca.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / "market-intel"
MANIFEST_DIR = PROJECT_ROOT / "data" / "market-reports"
MANIFEST_PATH = MANIFEST_DIR / "rag_load_manifest.json"
DEFAULT_BASE_URL = "http://127.0.0.1:5077"
COLLECTION = "skills"
MANIFEST_MAX_AGE_DAYS = 30

# --apify carrega so as regras de coleta (o minimo para autorizar coleta externa).
# Sem --apify, carrega o pacote inteiro da skill.
FILES_FULL = [
    SKILL_DIR / "SKILL.md",
    SKILL_DIR / "references" / "apify-rules.md",
    SKILL_DIR / "references" / "metrics-playbook.md",
]
FILES_APIFY = [
    SKILL_DIR / "references" / "apify-rules.md",
]

FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def read_env_token() -> str:
    """Token do ambiente ou, em fallback, do .env local (ECHOTIK_AGENT_TOKEN)."""
    tok = os.environ.get("ECHOTIK_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("ECHOTIK_AGENT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def chunk_markdown(path: Path) -> list[dict]:
    """Divide um .md por secoes '## '. Cada chunk traz o texto da secao junto do titulo."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = FRONTMATTER_RE.sub("", raw)
    rel = path.relative_to(PROJECT_ROOT).as_posix()

    lines = raw.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = "(topo)"
    current_body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_body:
                sections.append((current_title, current_body))
            current_title = line[3:].strip()
            current_body = [line]
        else:
            current_body.append(line)
    if current_body:
        sections.append((current_title, current_body))

    docs = []
    for title, body in sections:
        text = "\n".join(body).strip()
        if len(text) < 40:  # secao vazia ou separador
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "topo"
        docs.append(
            {
                "id": f"market-intel:{Path(rel).name}:{slug}",
                "text": text,
                "source": rel,
                "section": title,
                "commit": git_commit(),
                "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
    return docs


def load_manifest() -> dict | None:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def manifest_fresh(manifest: dict | None, commit: str) -> tuple[bool, str]:
    """(pode_pular, motivo). Pula se mesmo commit e manifesto com menos de 30 dias."""
    if manifest is None:
        return False, "sem manifesto anterior"
    if manifest.get("files") and not all(Path(f).exists() for f in manifest["files"]):
        return False, "arquivo do manifesto sumiu do disco"
    loaded = manifest.get("loaded_at", "")
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(loaded)
    except ValueError:
        return False, "manifesto sem data valida"
    if manifest.get("commit") != commit:
        return False, f"commit mudou ({manifest.get('commit')} -> {commit})"
    if age > timedelta(days=MANIFEST_MAX_AGE_DAYS):
        return False, f"manifesto com {age.days} dias (> {MANIFEST_MAX_AGE_DAYS})"
    return True, f"manifesto do commit {manifest.get('commit')} com {age.days} dias"


def post_ingest(docs: list[dict], base_url: str, token: str, timeout: int = 60) -> dict:
    url = f"{base_url.rstrip('/')}/rag/ingest"
    payload = json.dumps({"docs": docs, "collection": COLLECTION}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apify", action="store_true", help="carrega somente references/apify-rules.md (regra R2)")
    ap.add_argument("--force", action="store_true", help="recarrega mesmo com manifesto recente")
    ap.add_argument("--dry-run", action="store_true", help="mostra os chunks sem enviar")
    ap.add_argument("--base-url", default=os.environ.get("ECHOTIK_AGENT_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--token", default=None, help="default: $ECHOTIK_AGENT_TOKEN ou .env")
    args = ap.parse_args()

    files = FILES_APIFY if args.apify else FILES_FULL
    missing = [f for f in files if not f.exists()]
    if missing:
        print("ARQUIVOS AUSENTES:")
        for f in missing:
            print(f"  - {f}")
        return 2

    commit = git_commit()
    manifest = load_manifest()
    skip, reason = manifest_fresh(manifest, commit)
    if skip and not args.force:
        print(f"[pular] RAG ja carregado - {reason}")
        print(f"        (use --force para recarregar; manifesto: {MANIFEST_PATH})")
        return 0

    docs: list[dict] = []
    for f in files:
        chunks = chunk_markdown(f)
        print(f"[chunk] {f.relative_to(PROJECT_ROOT).as_posix()}: {len(chunks)} secoes")
        docs.extend(chunks)
    if not docs:
        print("nada para carregar (nenhuma secao com conteudo)")
        return 1

    if args.dry_run:
        for d in docs:
            preview = d["text"][:90].replace("\n", " ")
            print(f"  {d['id']}: {preview}...")
        print(f"\n[dry-run] {len(docs)} chunks; nada enviado.")
        return 0

    token = args.token or read_env_token()
    if not token:
        print("ERRO: token do sidecar ausente. Defina ECHOTIK_AGENT_TOKEN no ambiente ou no .env.")
        return 2

    # POST em lotes dentro do limite do endpoint (docs: max 200)
    ingested = 0
    for i in range(0, len(docs), 100):
        batch = docs[i : i + 100]
        try:
            out = post_ingest(batch, args.base_url, token)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            print(f"ERRO HTTP {e.code} em /rag/ingest: {body}")
            return 1
        except urllib.error.URLError as e:
            print(f"ERRO de rede em {args.base_url}: {e.reason}")
            return 1
        ingested += int(out.get("ingested", 0))
        print(f"[post] lote {i // 100 + 1}: {out.get('ingested', '?')} chunks ingested")

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    new_manifest = {
        "commit": commit,
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collection": COLLECTION,
        "files": [f.relative_to(PROJECT_ROOT).as_posix() for f in files],
        "doc_count": ingested,
        "scope": "apify-rules" if args.apify else "full",
    }
    MANIFEST_PATH.write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {ingested} chunks carregados na colecao '{COLLECTION}' (commit {commit})")
    print(f"     manifesto: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

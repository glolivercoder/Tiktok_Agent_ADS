"""RAG vetorial local (LanceDB). Embeddings determinísticos (hash) por padrão.

Segredos nunca entram no RAG. Conteúdo ingerido é dado não confiável.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

COLLECTIONS = ("skills", "personas", "research", "decisions")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}", (text or "").lower())


def embed(text: str, dim: int = 384) -> list[float]:
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class RAG:
    def __init__(self, path: Path, dim: int = 384) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.db = lancedb.connect(str(self.path))
        self._ensure()

    def _ensure(self) -> None:
        if "docs" not in self.db.table_names():
            schema = pa.schema(
                [
                    pa.field("id", pa.string()),
                    pa.field("collection", pa.string()),
                    pa.field("text", pa.string()),
                    pa.field("metadata", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), self.dim)),
                ]
            )
            self.db.create_table("docs", schema=schema)

    def _table(self):
        return self.db.open_table("docs")

    def ingest(self, docs: list[dict[str, Any]], collection: str = "skills") -> int:
        if collection not in COLLECTIONS:
            collection = "skills"
        rows = []
        for i, d in enumerate(docs):
            text = str(d.get("text") or d.get("content") or "")
            if not text.strip():
                continue
            meta = {k: v for k, v in d.items() if k not in ("text", "content")}
            rows.append(
                {
                    "id": str(d.get("id") or f"{collection}-{i}"),
                    "collection": collection,
                    "text": text,
                    "metadata": __import__("json").dumps(meta, ensure_ascii=False),
                    "vector": embed(text, self.dim),
                }
            )
        if not rows:
            return 0
        tbl = self._table()
        tbl.add(rows)
        return len(rows)

    def query(self, q: str, collection: str = "skills", limit: int = 5) -> list[dict[str, Any]]:
        import json as _j

        tbl = self._table()
        qv = embed(q, self.dim)
        try:
            res = tbl.search(qv).where(f"collection = '{collection}'").limit(limit).to_list()
        except Exception:
            res = []
        out = []
        for r in res:
            try:
                meta = _j.loads(r.get("metadata") or "{}")
            except Exception:
                meta = {}
            out.append(
                {
                    "id": r.get("id"),
                    "collection": r.get("collection"),
                    "text": r.get("text"),
                    "metadata": meta,
                    "score": float(r.get("_distance", 0.0)),
                }
            )
        # fallback lexical se vazio
        if not out:
            all_rows = tbl.to_arrow().to_pylist()
            cands = [r for r in all_rows if r.get("collection") == collection]
            if not cands:
                cands = all_rows
            scored = []
            for r in cands:
                sc = _cos(embed(str(r.get("text") or ""), self.dim), qv)
                scored.append((sc, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            for sc, r in scored[:limit]:
                try:
                    meta = _j.loads(r.get("metadata") or "{}")
                except Exception:
                    meta = {}
                out.append(
                    {
                        "id": r.get("id"),
                        "collection": r.get("collection"),
                        "text": r.get("text"),
                        "metadata": meta,
                        "score": float(sc),
                    }
                )
        return out

    def seed_personas(self, directory: Path, globs: list[str]) -> int:
        base = Path(directory)
        if not base.exists():
            return 0
        docs = []
        for g in globs:
            for p in base.glob(g):
                if not p.is_file():
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
                docs.append(
                    {
                        "id": str(p.relative_to(base)),
                        "text": text[:8000],
                        "source": str(p),
                        "name": p.stem,
                    }
                )
        return self.ingest(docs, collection="personas")

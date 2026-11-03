"""RAG vetorial local (LanceDB). Embeddings determinísticos (hash) por padrão.

Segredos nunca entram no RAG. Conteúdo ingerido é dado não confiável.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from .embedding import Embedder, HashEmbedder

COLLECTIONS = ("skills", "personas", "research", "decisions")


def embed(text: str, dim: int = 384) -> list[float]:
    """Compatibilidade: embedding hash local (usado em testes/fallback)."""
    return HashEmbedder(dim).embed(text)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class RAG:
    def __init__(
        self,
        path: Path,
        dim: int = 384,
        embedder: Embedder | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.embedder: Embedder = embedder or HashEmbedder(dim)
        self.dim = int(getattr(self.embedder, "dim", dim))
        self.db = lancedb.connect(str(self.path))
        self._ensure()

    @property
    def embedder_name(self) -> str:
        return str(getattr(self.embedder, "name", "hash"))

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
            return
        # Evita erro obscuro ao trocar ECHOTIK_EMBED_DIM com um LanceDB antigo.
        schema = self.db.open_table("docs").schema
        vector_type = schema.field("vector").type
        existing_dim = getattr(vector_type, "list_size", self.dim)
        if existing_dim != self.dim:
            raise RuntimeError(
                f"LanceDB em {self.path} tem vetores dim={existing_dim}, "
                f"mas o embedder atual usa dim={self.dim}. "
                "Use ECHOTIK_EMBED_DIM igual ou remova a pasta do LanceDB."
            )

    def _table(self):
        return self.db.open_table("docs")

    def count(self, collection: str | None = None) -> int:
        rows = self._table().to_arrow().to_pylist()
        if collection is None:
            return len(rows)
        return sum(1 for row in rows if row.get("collection") == collection)

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
                    "metadata": json.dumps(meta, ensure_ascii=False),
                    "vector": self.embedder.embed(text),
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
        qv = self.embedder.embed(q)
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
                sc = _cos(self.embedder.embed(str(r.get("text") or "")), qv)
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

    def seed_skills(self, directory: Path | None) -> int:
        """Ingere o catálogo 80/20 + markdowns de compliance/SEO uma única vez."""
        if not directory:
            return 0
        base = Path(directory)
        if not base.exists():
            return 0
        if self.count("skills") > 0:
            return 0

        docs: list[dict[str, Any]] = []
        catalog = base / "strategic_skills.json"
        if catalog.exists():
            try:
                raw = json.loads(catalog.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = []
            if isinstance(raw, list):
                for index, skill in enumerate(raw):
                    if not isinstance(skill, dict):
                        continue
                    parts = [
                        str(skill.get(key) or "")
                        for key in ("name", "when_to_use", "prompt")
                    ]
                    text = "\n".join(part for part in parts if part.strip())
                    if not text.strip():
                        continue
                    docs.append(
                        {
                            "id": f"skill-{skill.get('id') or index}",
                            "text": text[:8000],
                            "name": skill.get("name") or "",
                            "kind": "skill",
                        }
                    )
        for path in sorted(base.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if text.strip():
                docs.append(
                    {
                        "id": f"skill-md-{path.stem}",
                        "text": text[:8000],
                        "name": path.stem,
                        "kind": "skill",
                    }
                )
        return self.ingest(docs, collection="skills")

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

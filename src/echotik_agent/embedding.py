"""Embeddings do RAG: hash local (fallback) ou Gemini/OpenAI semântico.

O vetor é sempre normalizado e tem o mesmo `dim` do schema LanceDB. Se a
chamada remota falhar (chave inválida, cota, rede), cai no hash local em vez
de derrubar o sidecar. Nenhum texto de skill vira log.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

import httpx

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, text: str) -> list[float]: ...


def normalize(values: list[float]) -> list[float]:
    vec = [float(v) for v in values]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _fit(values: list[float], dim: int) -> list[float]:
    if len(values) > dim:
        values = values[:dim]
    elif len(values) < dim:
        values = values + [0.0] * (dim - len(values))
    return normalize(values)


class HashEmbedder:
    """Determinístico, sem rede. Útil para testes e como fallback."""

    name = "hash"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall((text or "").lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign
        return normalize(vec)


class GeminiEmbedder:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-004",
        dim: int = 384,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self._fallback = HashEmbedder(dim)

    def _request(self, text: str, with_dim: bool) -> list[float]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:embedContent"
        )
        body: dict[str, Any] = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": (text or "")[:8000]}]},
        }
        if with_dim:
            body["outputDimensionality"] = self.dim
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url,
                headers={"x-goog-api-key": self.api_key},
                json=body,
            )
            resp.raise_for_status()
            values = resp.json()["embedding"]["values"]
        return _fit([float(v) for v in values], self.dim)

    def embed(self, text: str) -> list[float]:
        try:
            return self._request(text, with_dim=True)
        except Exception:
            try:
                return self._request(text, with_dim=False)
            except Exception:
                return self._fallback.embed(text)


class OpenAIEmbedder:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: int = 384,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._fallback = HashEmbedder(dim)

    def embed(self, text: str) -> list[float]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "input": (text or "")[:8000],
                        "dimensions": self.dim,
                        "encoding_format": "float",
                    },
                )
                resp.raise_for_status()
                values = resp.json()["data"][0]["embedding"]
            return _fit([float(v) for v in values], self.dim)
        except Exception:
            return self._fallback.embed(text)


def build_embedder(settings: Any) -> Embedder:
    """Monta o embedder conforme Settings, com fallback hash."""
    provider = str(getattr(settings, "embedding_provider", "hash") or "hash").lower()
    dim = int(getattr(settings, "embedding_dim", 384) or 384)
    model = str(getattr(settings, "embedding_model", "") or "")
    timeout = float(getattr(settings, "embedding_timeout_s", 15.0) or 15.0)

    if provider == "gemini":
        key = str(getattr(settings, "gemini_api_key", "") or "")
        if key:
            return GeminiEmbedder(
                api_key=key,
                model=model or "text-embedding-004",
                dim=dim,
                timeout=timeout,
            )
    if provider == "openai":
        key = str(getattr(settings, "openai_api_key", "") or "")
        if key:
            return OpenAIEmbedder(
                api_key=key,
                model=model or "text-embedding-3-small",
                dim=dim,
                timeout=timeout,
                base_url=str(
                    getattr(settings, "openai_base_url", "https://api.openai.com/v1")
                ),
            )
    return HashEmbedder(dim)

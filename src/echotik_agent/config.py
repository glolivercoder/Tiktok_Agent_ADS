"""Configuração do sidecar. Falha fechado: sem token forte, o processo não sobe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Configuração ausente/inválida (o app não deve iniciar)."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser inteiro") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} deve estar entre {minimum} e {maximum}")
    return value


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path
    sqlite_path: Path
    lancedb_path: Path
    hermes_secrets_url: str = "http://127.0.0.1:5011"
    hermes_research_url: str = "http://127.0.0.1:5021"
    hermes_market_url: str = "http://127.0.0.1:5022"
    hermes_token: str = ""
    agency_agents_dir: Path | None = None
    embedding_dim: int = 384
    max_rag_hits: int = 5
    allow_unauthenticated_health: bool = True

    @classmethod
    def from_env(cls, data_dir: Path | None = None) -> "Settings":
        token = os.environ.get("ECHOTIK_AGENT_TOKEN", "").strip()
        if len(token) < 16:
            raise ConfigurationError(
                "ECHOTIK_AGENT_TOKEN ausente/curto (mínimo 16 chars; openssl rand -hex 32)"
            )
        base = data_dir or _env_path(
            "ECHOTIK_AGENT_DATA_DIR",
            Path(__file__).resolve().parents[2] / "data",
        )
        base.mkdir(parents=True, exist_ok=True)
        agency_raw = os.environ.get("AGENCY_AGENTS_DIR", "").strip()
        return cls(
            token=token,
            data_dir=base,
            sqlite_path=_env_path("ECHOTIK_SQLITE_PATH", base / "echotik.db"),
            lancedb_path=_env_path("ECHOTIK_LANCEDB_PATH", base / "lancedb"),
            hermes_secrets_url=os.environ.get(
                "HERMES_SECRETS_URL", "http://127.0.0.1:5011"
            ).strip(),
            hermes_research_url=os.environ.get(
                "HERMES_RESEARCH_URL", "http://127.0.0.1:5021"
            ).strip(),
            hermes_market_url=os.environ.get(
                "HERMES_MARKET_URL", "http://127.0.0.1:5022"
            ).strip(),
            hermes_token=os.environ.get("HERMES_HTTP_TOKEN", "").strip(),
            agency_agents_dir=Path(agency_raw).expanduser() if agency_raw else None,
            embedding_dim=_env_int("ECHOTIK_EMBED_DIM", 384, 64, 1536),
            max_rag_hits=_env_int("ECHOTIK_MAX_RAG_HITS", 5, 1, 20),
            allow_unauthenticated_health=_env_bool("ECHOTIK_OPEN_HEALTH", True),
        )


def load_settings() -> Settings:
    return Settings.from_env()

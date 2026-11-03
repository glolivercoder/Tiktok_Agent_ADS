"""Configuração do sidecar. Falha fechado: sem token forte, o processo não sobe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env da raiz do projeto (se existir)
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


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


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser numérico") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} deve estar entre {minimum} e {maximum}")
    return value


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path
    sqlite_path: Path
    lancedb_path: Path
    skills_dir: Path | None = None
    hermes_secrets_url: str = "http://127.0.0.1:5011"
    hermes_research_url: str = "http://127.0.0.1:5021"
    hermes_market_url: str = "http://127.0.0.1:5022"
    hermes_token: str = ""
    hermes_timeout_s: float = 15.0
    hermes_market_timeout_s: float = 90.0
    agency_agents_dir: Path | None = None
    embedding_dim: int = 384
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-004"
    embedding_timeout_s: float = 15.0
    gemini_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    echotok_base_url: str = "https://open.echotik.live/api/v3"
    echotok_api_key: str = ""
    echotok_api_user: str = ""
    echotok_api_password: str = ""
    echotok_region: str = "BR"
    echotok_timeout_s: float = 15.0
    echotok_daily_limit: int = 50
    max_rag_hits: int = 5
    allow_unauthenticated_health: bool = True
    # LLM (OmniRoute primeiro, OpenRouter como fallback)
    llm_enabled: bool = True
    llm_timeout_s: float = 20.0
    llm_max_tokens: int = 700
    llm_temperature: float = 0.4
    omni_base_url: str = "http://localhost:20128/v1"
    omni_api_key: str = ""
    omni_model: str = "auto/best-free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/auto"
    cors_origins: str = "*"
    rate_limit_per_min: int = 120
    rate_limit_burst: int = 30

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
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        embedding_provider = (
            os.environ.get("ECHOTIK_EMBED_PROVIDER", "").strip().lower()
            or ("gemini" if gemini_key else "openai" if openai_key else "hash")
        )
        embedding_model_default = (
            "text-embedding-004"
            if embedding_provider == "gemini"
            else "text-embedding-3-small"
            if embedding_provider == "openai"
            else "hash-384"
        )
        skills_path_raw = os.environ.get("ECHOTIK_SKILLS_PATH", "").strip()
        skills_dir_raw = os.environ.get("ECHOTIK_SKILLS_DIR", "").strip()
        if skills_dir_raw:
            skills_dir = Path(skills_dir_raw).expanduser()
        elif skills_path_raw:
            skills_dir = Path(skills_path_raw).expanduser().parent
        else:
            skills_dir = base / "skills"
        return cls(
            token=token,
            data_dir=base,
            sqlite_path=_env_path("ECHOTIK_SQLITE_PATH", base / "echotik.db"),
            lancedb_path=_env_path("ECHOTIK_LANCEDB_PATH", base / "lancedb"),
            skills_dir=skills_dir,
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
            embedding_provider=embedding_provider,
            embedding_model=(
                os.environ.get("ECHOTIK_EMBED_MODEL", "").strip()
                or embedding_model_default
            ),
            embedding_timeout_s=_env_float("ECHOTIK_EMBED_TIMEOUT", 15.0, 1.0, 60.0),
            gemini_api_key=gemini_key,
            openai_api_key=openai_key,
            openai_base_url=_env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            echotok_base_url=_env_str(
                "ECHOTIK_API_BASE_URL", "https://open.echotik.live/api/v3"
            ),
            echotok_api_key=os.environ.get("ECHOTOK_API_KEY", "").strip(),
            echotok_api_user=os.environ.get("ECHOTOK_API_USER", "").strip(),
            echotok_api_password=os.environ.get("ECHOTOK_API_PASSWORD", "").strip(),
            echotok_region=_env_str("ECHOTIK_REGION", "BR"),
            echotok_timeout_s=_env_float("ECHOTIK_API_TIMEOUT", 15.0, 1.0, 60.0),
            echotok_daily_limit=_env_int("ECHOTIK_API_DAILY_LIMIT", 50, 1, 10_000),
            hermes_timeout_s=_env_float("HERMES_HTTP_TIMEOUT", 15.0, 1.0, 60.0),
            hermes_market_timeout_s=_env_float("HERMES_MARKET_TIMEOUT", 90.0, 10.0, 300.0),
            max_rag_hits=_env_int("ECHOTIK_MAX_RAG_HITS", 5, 1, 20),
            allow_unauthenticated_health=_env_bool("ECHOTIK_OPEN_HEALTH", True),
            llm_enabled=_env_bool("ECHOTIK_LLM_ENABLED", True),
            llm_timeout_s=_env_float("ECHOTIK_LLM_TIMEOUT", 20.0, 1.0, 120.0),
            llm_max_tokens=_env_int("ECHOTIK_LLM_MAX_TOKENS", 700, 64, 4096),
            llm_temperature=_env_float("ECHOTIK_LLM_TEMPERATURE", 0.4, 0.0, 2.0),
            omni_base_url=_env_str("OMNIROUTE_BASE_URL", "http://localhost:20128/v1"),
            omni_api_key=os.environ.get("OMNIROUTE_API_KEY", "").strip(),
            omni_model=_env_str("OMNIROUTE_MODEL", "auto/best-free"),
            openrouter_base_url=_env_str(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=_env_str("OPENROUTER_MODEL", "openrouter/auto"),
            cors_origins=_env_str("ECHOTIK_CORS_ORIGINS", "*"),
            rate_limit_per_min=_env_int("ECHOTIK_RATE_LIMIT_PER_MIN", 120, 0, 10_000),
            rate_limit_burst=_env_int("ECHOTIK_RATE_LIMIT_BURST", 30, 1, 10_000),
        )


def load_settings() -> Settings:
    return Settings.from_env()

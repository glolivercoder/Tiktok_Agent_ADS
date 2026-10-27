"""EchoTok Agent sidecar — SQLite + LanceDB RAG + regras de campanha.

Serviço Python externo ao núcleo Hermes (ADR-001): LangGraph/LlamaIndex, se
vierem, ficam só aqui atrás de HTTP. Segredos nunca entram no RAG.
"""

from .config import ConfigurationError, Settings
from .main import create_app

__all__ = ["ConfigurationError", "Settings", "create_app"]
__version__ = "0.1.0"

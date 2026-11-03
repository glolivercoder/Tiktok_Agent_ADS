"""Cliente LLM opcional (OpenAI-compatible).

Ordem: OmniRoute (free/credits) → OpenRouter (fallback). Se nenhum provedor
estiver configurado ou todos falharem, o chamador cai nas regras locais.

Segredos nunca são logados nem devolvidos ao cliente.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Settings


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str


class LLMClient:
    """Fala com qualquer endpoint compatível com /chat/completions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self.settings.llm_enabled and bool(
            self.settings.omni_api_key or self.settings.openrouter_api_key
        )

    def _candidates(self) -> list[tuple[str, str, str, str]]:
        out: list[tuple[str, str, str, str]] = []
        if self.settings.omni_api_key:
            out.append(
                (
                    "omniroute",
                    self.settings.omni_base_url,
                    self.settings.omni_api_key,
                    self.settings.omni_model,
                )
            )
        if self.settings.openrouter_api_key:
            out.append(
                (
                    "openrouter",
                    self.settings.openrouter_base_url,
                    self.settings.openrouter_api_key,
                    self.settings.openrouter_model,
                )
            )
        return out

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult | None:
        """Tenta cada provedor em ordem. Retorna None se todos falharem."""
        if not self.available:
            return None
        self.last_error = None
        for provider, base, key, model in self._candidates():
            try:
                text = await self._call(
                    provider,
                    base,
                    key,
                    model,
                    messages,
                    max_tokens or self.settings.llm_max_tokens,
                    self.settings.llm_temperature
                    if temperature is None
                    else temperature,
                )
            except Exception as exc:  # noqa: BLE001 - fallback silencioso
                # Só o tipo do erro: nunca a URL com credenciais nem headers.
                self.last_error = f"{provider}:{type(exc).__name__}"
                continue
            if text:
                return LLMResult(text=text, provider=provider, model=model)
        return None

    async def _call(
        self,
        provider: str,
        base: str,
        key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str | None:
        url = base.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/glolivercoder/Tiktok_Agent_ADS"
            headers["X-Title"] = "EchoTok Hub"

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_s) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
        if not content:
            # Alguns modelos de reasoning colocam o texto aqui e deixam content vazio.
            content = message.get("reasoning_content")
        if not isinstance(content, str):
            return None
        text = content.strip()
        return text or None


class NullLLM:
    """Implementação nula para testes: nunca chama a rede."""

    available = False
    last_error = None

    async def complete(self, *_args, **_kwargs) -> LLMResult | None:
        return None

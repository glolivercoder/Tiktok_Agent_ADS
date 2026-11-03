"""Motor de templates Jinja2 para mensagens personalizadas."""
from __future__ import annotations

import logging
from typing import Any, Optional

from jinja2 import Environment, StrictUndefined, TemplateError as JinjaTemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

from .models import Template, Contact

logger = logging.getLogger(__name__)


class TemplateError(Exception):
    """Erro no processamento de template."""
    def __init__(self, message: str, template_id: str, original_error: Exception | None = None):
        super().__init__(message)
        self.template_id = template_id
        self.original_error = original_error


class TemplateEngine:
    """Motor de renderização de templates Jinja2 com sandbox."""

    def __init__(self, sandboxed: bool = True) -> None:
        if sandboxed:
            self.env = SandboxedEnvironment(
                undefined=StrictUndefined,
                autoescape=True,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        else:
            self.env = Environment(
                undefined=StrictUndefined,
                autoescape=True,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        # Filtros personalizados
        self.env.filters["brl"] = self._fmt_brl
        self.env.filters["pct"] = self._fmt_pct
        self.env.filters["title_case"] = str.title
        self.env.filters["upper"] = str.upper
        self.env.filters["lower"] = str.lower
        self.env.filters["truncate"] = self._truncate
        self.env.filters["default_if_empty"] = self._default_if_empty

    def _fmt_brl(self, value: Any) -> str:
        """Formata valor como BRL."""
        try:
            return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return str(value)

    def _fmt_pct(self, value: Any) -> str:
        """Formata valor como porcentagem."""
        try:
            return f"{float(value):.1f}%".replace(".", ",")
        except (ValueError, TypeError):
            return str(value)

    def _truncate(self, value: str, length: int = 100, suffix: str = "...") -> str:
        """Trunca string."""
        if not isinstance(value, str):
            value = str(value)
        if len(value) <= length:
            return value
        return value[:length - len(suffix)] + suffix

    def _default_if_empty(self, value: Any, default: str = "") -> str:
        """Retorna default se vazio."""
        if value is None or value == "":
            return default
        return str(value)

    def extract_variables(self, template_str: str) -> set[str]:
        """Extrai variáveis não declaradas do template."""
        ast = self.env.parse(template_str)
        return meta.find_undeclared_variables(ast)

    def validate_template(self, template: Template) -> tuple[bool, list[str]]:
        """Valida template e retorna (válido, lista de erros)."""
        errors = []

        try:
            self.env.parse(template.body)
        except JinjaTemplateError as e:
            errors.append(f"Erro de sintaxe: {e}")

        # Verifica variáveis declaradas vs usadas
        used_vars = self.extract_variables(template.body)
        declared_vars = set(template.variables) if template.variables else set()
        undeclared = used_vars - declared_vars
        if undeclared:
            errors.append(f"Variáveis não declaradas: {', '.join(sorted(undeclared))}")

        return len(errors) == 0, errors

    def render(self, template: Template, contact: Any, extra_context: dict[str, Any] | None = None) -> str:
        """Renderiza template com dados do contato."""
        context = self._build_context(contact, extra_context)

        try:
            tpl = self.env.from_string(template.body)
            return tpl.render(**context)
        except JinjaTemplateError as e:
            raise TemplateError(
                f"Erro ao renderizar template: {e}",
                template.id,
                e,
            )
        except Exception as e:
            raise TemplateError(
                f"Erro inesperado ao renderizar: {e}",
                template.id,
                e,
            )

    def _build_context(self, contact: Any, extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Constrói contexto de renderização a partir do contato."""
        ctx = {
            "nome": getattr(contact, "name", "") or "",
            "nome_primeiro": self._get_first_name(getattr(contact, "name", "")),
            "telefone": getattr(contact, "phone", "") or "",
            "wechat_id": getattr(contact, "wechat_id", "") or "",
            "email": getattr(contact, "email", "") or "",
            "tiktok": getattr(contact, "tiktok_username", "") or "",
            "tiktok_username": getattr(contact, "tiktok_username", "") or "",
        }

        # Adiciona metadata se existir
        metadata = getattr(contact, "metadata", {}) or {}
        for key, value in metadata.items():
            if key not in ctx:
                ctx[key] = value

        if extra_context:
            ctx.update(extra_context)

        return ctx

    def _get_first_name(self, full_name: str) -> str:
        """Extrai primeiro nome."""
        if not full_name:
            return ""
        parts = full_name.strip().split()
        return parts[0] if parts else ""

    def render_batch(
        self,
        template: Template,
        contacts: list[Any],
        extra_context: dict[str, Any] | None = None,
    ) -> list[tuple[str, str | None]]:
        """Renderiza template para lote de contatos. Retorna (mensagem, erro)."""
        results = []
        for contact in contacts:
            try:
                message = self.render(template, contact, extra_context)
                results.append((contact.id, message))
            except Exception as e:
                logger.warning(f"Erro ao renderizar para contato {contact.id}: {e}")
                results.append((contact.id, None))
        return results
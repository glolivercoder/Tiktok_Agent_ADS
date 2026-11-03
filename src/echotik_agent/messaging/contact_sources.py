"""Resolvedores de fontes de contatos (CSV, Google Sheets, API, Manual)."""
from __future__ import annotations

import csv
import io
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from .models import Contact, ContactSource, ContactSourceConfig

logger = logging.getLogger(__name__)


class ContactSourceError(Exception):
    """Erro ao resolver fonte de contatos."""
    def __init__(self, message: str, source: ContactSource, original_error: Exception | None = None):
        super().__init__(message)
        self.source = source
        self.original_error = original_error


class ContactSourceResolver(ABC):
    """Interface base para resolvedores de contatos."""

    @abstractmethod
    async def resolve(self, config: ContactSourceConfig) -> list[dict[str, Any]]:
        """Resolve a fonte e retorna lista de dicionários com dados brutos."""
        ...

    def _map_columns(self, row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
        """Mapeia colunas da fonte para campos do Contact."""
        result = {}
        for target_field, source_column in mapping.items():
            if source_column in row:
                result[target_field] = row[source_column]
        return result

    def _normalize_contact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normaliza dados do contato."""
        return {
            "name": data.get("name", "").strip() if data.get("name") else "",
            "phone": data.get("phone", "").strip() if data.get("phone") else None,
            "wechat_id": data.get("wechat_id", "").strip() if data.get("wechat_id") else None,
            "email": data.get("email", "").strip().lower() if data.get("email") else None,
            "tiktok_username": data.get("tiktok_username", "").strip().lstrip("@") if data.get("tiktok_username") else None,
            "metadata": {k: v for k, v in data.items() if k not in ("name", "phone", "wechat_id", "email", "tiktok_username")},
        }


class CSVContactResolver(ContactSourceResolver):
    """Resolvedor para arquivos CSV."""

    async def resolve(self, config: ContactSourceConfig) -> list[dict[str, Any]]:
        if not config.csv_path:
            raise ContactSourceError("csv_path não configurado", ContactSource.CSV_UPLOAD)

        path = Path(config.csv_path)
        if not path.exists():
            raise ContactSourceError(f"Arquivo CSV não encontrado: {path}", ContactSource.CSV_UPLOAD)

        contacts = []
        try:
            with open(path, newline="", encoding=config.csv_encoding) as f:
                # Detecta delimitador automaticamente
                sample = f.read(1024)
                f.seek(0)
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(sample).delimiter if sniffer.has_header else config.csv_delimiter

                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    mapped = self._map_columns(row, config.column_mapping)
                    normalized = self._normalize_contact(mapped)
                    if normalized["name"]:  # Só adiciona se tiver nome
                        contacts.append(normalized)

            logger.info(f"CSV resolvido: {len(contacts)} contatos de {config.csv_path}")
            return contacts

        except csv.Error as e:
            raise ContactSourceError(f"Erro ao parsear CSV: {e}", ContactSource.CSV_UPLOAD, e)
        except Exception as e:
            raise ContactSourceError(f"Erro ao ler CSV: {e}", ContactSource.CSV_UPLOAD, e)


class GoogleSheetsContactResolver(ContactSourceResolver):
    """Resolvedor para Google Sheets."""

    def __init__(self) -> None:
        self._gc: Optional[gspread.Client] = None

    def _get_client(self, config: ContactSourceConfig) -> gspread.Client:
        if self._gc is not None:
            return self._gc

        if not config.sheets_credentials_path:
            raise ContactSourceError(
                "sheets_credentials_path não configurado",
                ContactSource.GOOGLE_SHEETS
            )

        creds_path = Path(config.sheets_credentials_path)
        if not creds_path.exists():
            raise ContactSourceError(
                f"Arquivo de credenciais não encontrado: {creds_path}",
                ContactSource.GOOGLE_SHEETS
            )

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        self._gc = gspread.authorize(creds)
        return self._gc

    async def resolve(self, config: ContactSourceConfig) -> list[dict[str, Any]]:
        if not config.sheets_url:
            raise ContactSourceError("sheets_url não configurado", ContactSource.GOOGLE_SHEETS)

        try:
            gc = self._get_client(config)
            spreadsheet = gc.open_by_url(config.sheets_url)
            worksheet = spreadsheet.sheet1  # Primeira aba

            # Obter todos os valores
            range_name = config.sheets_range
            rows = worksheet.get(range_name)

            if not rows:
                logger.warning("Planilha vazia")
                return []

            # Primeira linha = headers
            headers = rows[0]
            data_rows = rows[1:]

            contacts = []
            for row in data_rows:
                # Completa colunas faltantes
                row_dict = dict(zip(headers, row + [""] * (len(headers) - len(row))))
                mapped = self._map_columns(row_dict, config.column_mapping)
                normalized = self._normalize_contact(row_dict)
                if normalized["name"]:
                    contacts.append(normalized)

            logger.info(f"Google Sheets resolvido: {len(contacts)} contatos")
            return contacts

        except gspread.exceptions.GSpreadException as e:
            raise ContactSourceError(f"Erro no Google Sheets: {e}", ContactSource.GOOGLE_SHEETS, e)
        except Exception as e:
            raise ContactSourceError(f"Erro ao acessar Google Sheets: {e}", ContactSource.GOOGLE_SHEETS, e)


class APIContactResolver(ContactSourceResolver):
    """Resolvedor para API externa."""

    async def resolve(self, config: ContactSourceConfig) -> list[dict[str, Any]]:
        if not config.api_url:
            raise ContactSourceError("api_url não configurado", ContactSource.API)

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    config.api_url,
                    headers=config.api_headers or {},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                if not isinstance(data, list):
                    raise ContactSourceError("API deve retornar lista de contatos", ContactSource.API)

                contacts = []
                for item in data:
                    if isinstance(item, dict):
                        mapped = self._map_columns(item, config.column_mapping)
                        normalized = self._normalize_contact(mapped)
                        if normalized["name"]:
                            contacts.append(normalized)

                logger.info(f"API resolvida: {len(contacts)} contatos")
                return contacts

            except httpx.HTTPError as e:
                raise ContactSourceError(f"Erro HTTP na API: {e}", ContactSource.API, e)
            except Exception as e:
                raise ContactSourceError(f"Erro na API: {e}", ContactSource.API, e)


class ManualContactResolver(ContactSourceResolver):
    """Resolvedor para contatos manuais (lista direta)."""

    async def resolve(self, config: ContactSourceConfig) -> list[dict[str, Any]]:
        # Para contatos manuais, espera-se que os dados já venham no metadata do config
        # ou sejam passados separadamente. Aqui apenas valida.
        contacts = []
        # Se houver dados no metadata, usa eles
        if hasattr(config, 'metadata') and config.metadata:
            # Lógica futura para contatos manuais inline
            pass
        return contacts


class ContactSourceResolverFactory:
    """Factory para criar resolvedores baseados no tipo de fonte."""

    _resolvers: dict[ContactSource, type[ContactSourceResolver]] = {
        ContactSource.CSV_UPLOAD: CSVContactResolver,
        ContactSource.GOOGLE_SHEETS: GoogleSheetsContactResolver,
        ContactSource.API: APIContactResolver,
        ContactSource.MANUAL: ManualContactResolver,
    }

    @classmethod
    def create(cls, source: ContactSource) -> ContactSourceResolver:
        resolver_class = cls._resolvers.get(source)
        if not resolver_class:
            raise ContactSourceError(f"Fonte não suportada: {source}", source)
        return resolver_class()

    @classmethod
    async def resolve_all(cls, configs: list[ContactSourceConfig]) -> list[dict[str, Any]]:
        """Resolve múltiplas fontes e combina resultados."""
        all_contacts = []
        for config in configs:
            resolver = cls.create(config.source)
            contacts = await resolver.resolve(config)
            # Adiciona source_id para rastreamento
            for contact in contacts:
                contact["source"] = config.source.value
            all_contacts.extend(contacts)
        return all_contacts
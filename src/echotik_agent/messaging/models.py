"""Modelos Pydantic para Mass Messaging HITL."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ContactSource(str, enum.Enum):
    CSV_UPLOAD = "csv_upload"
    GOOGLE_SHEETS = "google_sheets"
    API = "api"
    MANUAL = "manual"


class BatchStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


class Contact(BaseModel):
    """Contato para prospecção."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    phone: Optional[str] = None
    wechat_id: Optional[str] = None  # WeChat ID ou wxid
    email: Optional[str] = None
    tiktok_username: Optional[str] = None
    source: ContactSource = ContactSource.MANUAL
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("wechat_id", mode="before")
    @classmethod
    def _normalize_wechat_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() if v.strip() else None


class ContactSourceConfig(BaseModel):
    """Configuração de fonte de contatos."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    source: ContactSource
    # CSV
    csv_path: Optional[str] = None
    csv_encoding: str = "utf-8"
    csv_delimiter: str = ","
    # Google Sheets
    sheets_url: Optional[str] = None
    sheets_range: str = "A:Z"
    sheets_credentials_path: Optional[str] = None
    # API
    api_url: Optional[str] = None
    api_headers: dict[str, str] = Field(default_factory=dict)
    # Mapeamento de colunas
    column_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "name": "nome",
            "phone": "telefone",
            "wechat_id": "wechat_id",
            "email": "email",
            "tiktok_username": "tiktok",
        }
    )


class Template(BaseModel):
    """Template Jinja2 para mensagens."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    id: str = Field(default_factory=lambda: f"tpl_{uuid.uuid4().hex[:8]}")
    name: str
    subject: Optional[str] = None  # Opcional, para email
    body: str  # Template Jinja2
    variables: list[str] = Field(default_factory=list)  # Lista de variáveis esperadas
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MessageQueueItem(BaseModel):
    """Item individual na fila de envio."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    batch_id: str
    contact: Contact
    template_id: str
    original_message: str
    current_message: str
    status: MessageStatus = MessageStatus.PENDING
    scheduled_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    attempts: int = 0
    last_error: Optional[str] = None
    wechaty_response: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BatchReview(BaseModel):
    """Lote para revisão humana (HITL)."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    id: str = Field(default_factory=lambda: f"batch_{uuid.uuid4().hex[:8]}")
    name: Optional[str] = None
    source: ContactSource
    source_config: ContactSourceConfig
    template_id: str
    template_snapshot: Template  # Snapshot do template no momento da criação
    contacts: list[Contact] = Field(default_factory=list)
    generated_messages: list[MessageQueueItem] = Field(default_factory=list)
    status: BatchStatus = BatchStatus.PENDING_REVIEW
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_ids: list[str] = Field(default_factory=list)
    rejected_ids: list[str] = Field(default_factory=list)
    edited_messages: dict[str, str] = Field(default_factory=dict)  # item_id -> edited_message
    scheduled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_contacts(self) -> int:
        return len(self.contacts)

    @property
    def approved_count(self) -> int:
        return len(self.approved_ids)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_ids)

    @property
    def pending_count(self) -> int:
        return self.total_contacts - self.approved_count - self.rejected_count


class WechatyConfig(BaseModel):
    """Configuração do Wechaty."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    puppet: str = "wechaty-puppet-wechat"  # ou wechaty-puppet-padlocal, wechaty-puppet-service
    puppet_token: Optional[str] = None
    name: str = "EchoTokMassMessaging"
    headless: bool = True
    # Business hours
    business_hours_start: int = 9  # 9h
    business_hours_end: int = 18   # 18h
    timezone: str = "America/Sao_Paulo"
    # Delays aleatórios
    min_delay_seconds: int = 300   # 5 min
    max_delay_seconds: int = 720   # 12 min
    # Retry
    max_retries: int = 3
    retry_base_delay_seconds: int = 60
    # Rate limit
    max_concurrent_sends: int = 1


class QueueConfig(BaseModel):
    """Configuração do scheduler de fila."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    check_interval_seconds: int = 30
    business_hours_start: int = 9
    business_hours_end: int = 18
    timezone: str = "America/Sao_Paulo"
    min_delay_seconds: int = 300
    max_delay_seconds: int = 720
    max_concurrent_sends: int = 1
    max_retries: int = 3
    retry_base_delay_seconds: int = 60
    dlq_max_size: int = 1000
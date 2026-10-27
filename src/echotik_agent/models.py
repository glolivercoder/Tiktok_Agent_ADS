"""Contratos estritos do sidecar (camelCase no JSON, snake_case no Python)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CampaignStatus(StrEnum):
    RASCUNHO = "rascunho"
    ATIVA = "ativa"
    PAUSADA = "pausada"


class CampaignTipo(StrEnum):
    PAGO = "pago"
    ORGANICO = "organico"


class DecisionAction(StrEnum):
    PAUSAR = "pausar"
    REFORCAR = "reforcar"
    TROCAR_CRIATIVO = "trocar_criativo"
    REDUZIR_LANCE = "reduzir_lance"
    ESCALAR = "escalar"
    AGUARDAR = "aguardar"
    OUTRO = "outro"


class CampaignIn(ApiModel):
    id: str = Field(min_length=1, max_length=80)
    nome: str = Field(min_length=1, max_length=200)
    produto_id: str | None = None
    tipo: CampaignTipo = CampaignTipo.PAGO
    status: CampaignStatus = CampaignStatus.ATIVA
    gasto7: float = Field(default=0, ge=0)
    receita7: float = Field(default=0, ge=0)
    alvo_roas: float | None = Field(default=None, ge=0)
    alvo_acos: float | None = Field(default=None, ge=0, le=1)
    nota: str = ""
    plataforma: str = ""


class CampaignOut(CampaignIn):
    roas: float | None = None
    acos: float | None = None


class ProductContext(ApiModel):
    id: str = "p1"
    nome: str = "produto"
    preco: float = 0
    comissao_pct: float = 0
    comissao_un: float = 0
    unidades30: int = 0
    temp: float = 0
    growth7: float = 0
    hooks: list[str] = Field(default_factory=list)
    sazonal: list[float] | None = None
    vel: list[float] | None = None


class NicheContext(ApiModel):
    nome: str
    demanda: float = 0
    conc: float = 0
    score: float = 0


class SubnichoContext(ApiModel):
    nome: str
    growth30: float = 0


class SessionSyncRequest(ApiModel):
    campaigns: list[CampaignIn] = Field(default_factory=list)
    selected: ProductContext | None = None
    products: list[ProductContext] = Field(default_factory=list)
    niches: list[NicheContext] = Field(default_factory=list)
    subnichos: list[SubnichoContext] = Field(default_factory=list)
    checklist: list[bool] = Field(default_factory=list)
    fonte: str = "simulado"


class ChatRequest(ApiModel):
    message: str = Field(min_length=1, max_length=4000)
    session: SessionSyncRequest | None = None
    use_rag: bool = True
    limit: int = Field(default=5, ge=1, le=20)


class Suggestion(ApiModel):
    action: DecisionAction
    reason: str
    campaign_id: str | None = None
    requires_approval: bool = True
    confidence: float = Field(default=0.7, ge=0, le=1)


class ChatResponse(ApiModel):
    ok: bool = True
    reply: str
    mode: str = "rules"
    suggestions: list[Suggestion] = Field(default_factory=list)
    rag_hits: list[dict[str, Any]] = Field(default_factory=list)
    untrusted: bool = True
    fails: list[CampaignOut] = Field(default_factory=list)


class DiagnoseRequest(ApiModel):
    session: SessionSyncRequest | None = None
    campaign_id: str | None = None


class DiagnoseResponse(ApiModel):
    ok: bool = True
    fails: list[CampaignOut]
    suggestions: list[Suggestion]
    avg_roas: float
    reply: str


class DecisionIn(ApiModel):
    campaign_id: str | None = None
    action: DecisionAction
    reason: str = Field(min_length=1, max_length=2000)
    metrics: dict[str, float] = Field(default_factory=dict)
    outcome_roas_3d: float | None = None
    outcome_roas_7d: float | None = None
    approved: bool | None = None


class DecisionOut(DecisionIn):
    id: int
    created_at: str


class RememberRequest(ApiModel):
    kind: str = Field(default="context", min_length=1, max_length=40)
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list)
    project: str = "echotik"


class RecallRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    project: str = "echotik"
    kind: str | None = None


class RagIngestRequest(ApiModel):
    docs: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    collection: str = "skills"


class RagQueryRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    collection: str = "skills"
    limit: int = Field(default=5, ge=1, le=20)


class SeedPersonasRequest(ApiModel):
    directory: str | None = None
    globs: list[str] = Field(
        default_factory=lambda: [
            "marketing/marketing-tiktok-strategist.md",
            "marketing/marketing-douyin-strategist.md",
            "marketing/marketing-short-video-editing-coach.md",
            "marketing/marketing-livestream-commerce-coach.md",
            "marketing/marketing-cross-border-ecommerce.md",
            "paid-media/paid-media-auditor.md",
            "paid-media/paid-media-creative-strategist.md",
            "paid-media/paid-media-paid-social-strategist.md",
            "research/research-synthesist.md",
        ]
    )


class ErrorBody(ApiModel):
    error: str
    message: str

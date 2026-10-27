"""Regras de campanha + diagnóstico + chat por regras (sem LLM)."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    CampaignIn,
    CampaignOut,
    ChatResponse,
    DecisionAction,
    DiagnoseResponse,
    SessionSyncRequest,
    Suggestion,
)


def calc_campaign(c: CampaignIn | dict[str, Any]) -> CampaignOut:
    data = c if isinstance(c, dict) else c.model_dump()
    gasto = float(data.get("gasto7") or 0)
    receita = float(data.get("receita7") or 0)
    roas = round(receita / gasto, 2) if gasto > 0 else None
    acos = round(gasto / receita, 4) if receita > 0 else None
    return CampaignOut(**{**data, "roas": roas, "acos": acos})


def fail_guard(campaigns: list[CampaignIn | dict[str, Any]]) -> list[CampaignOut]:
    out: list[CampaignOut] = []
    for c in campaigns:
        co = calc_campaign(c)
        if co.tipo != "pago":
            continue
        if not co.gasto7 or co.gasto7 <= 0:
            continue
        if co.status == "pausada":
            continue
        bad = (co.alvo_roas is not None and co.roas is not None and co.roas < co.alvo_roas) or (
            co.alvo_acos is not None and co.acos is not None and co.acos > co.alvo_acos
        )
        if bad:
            out.append(co)
    return out


def avg_roas(campaigns: list[CampaignIn | dict[str, Any]]) -> float:
    vals = []
    for c in campaigns:
        co = calc_campaign(c)
        if co.roas is not None:
            vals.append(co.roas)
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def diagnose(session: SessionSyncRequest | None) -> DiagnoseResponse:
    campaigns = (session.campaigns if session else []) or []
    fails = fail_guard(campaigns)
    ar = avg_roas(campaigns)
    suggestions: list[Suggestion] = []
    for c in fails:
        if c.alvo_roas is not None and c.roas is not None and c.roas < c.alvo_roas * 0.5:
            suggestions.append(
                Suggestion(
                    action=DecisionAction.PAUSAR,
                    reason=f"{c.nome}: ROAS {c.roas}x abaixo da metade do alvo {c.alvo_roas}x",
                    campaign_id=c.id,
                    confidence=0.9,
                )
            )
        else:
            suggestions.append(
                Suggestion(
                    action=DecisionAction.TROCAR_CRIATIVO,
                    reason=f"{c.nome}: ROAS {c.roas}x vs alvo {c.alvo_roas}x — trocar gancho dos 2s",
                    campaign_id=c.id,
                    confidence=0.75,
                )
            )
    if fails:
        reply = (
            f"{len(fails)} campanha(s) acionaram a proteção (ROAS abaixo do alvo por 3 dias): "
            + ", ".join(f"{c.nome} ({c.roas}x)" for c in fails)
            + f". ROAS médio da conta: {ar}x."
        )
    else:
        reply = f"Nenhuma campanha fora do alvo agora. ROAS médio {ar}x contra alvo 2,0x."
    return DiagnoseResponse(
        fails=fails,
        suggestions=suggestions,
        avg_roas=ar,
        reply=reply,
    )


def answer(q: str, session: SessionSyncRequest | None, rag_hits: list[dict[str, Any]] | None = None) -> ChatResponse:
    t = (q or "").lower()
    p = session.selected if session else None
    campaigns = (session.campaigns if session else []) or []
    fails = fail_guard(campaigns)
    ca_roas = avg_roas(campaigns)
    rag_hits = rag_hits or []

    def A(s: str) -> str:
        return f"<strong>{s}</strong>"

    if re.search(r"roteir|gancho|hook|v[íi]deo", t):
        if not p or not p.hooks:
            reply = "O produto selecionado veio sem ganchos. Me dê uma frase descrevendo o produto que eu monto o roteiro a partir dela."
        else:
            reply = (
                f"Roteiro de 15s para {A(p.nome)}: 0-2s gancho \u201C{p.hooks[0]}\u201D; "
                f"3-7s problema; 8-12s prova; 13-15s CTA \u201Cclica na sacolinha amarela\u201D. "
                f"Grave 3 variações só do gancho."
            )
    elif re.search(r"margem|comiss|lucro|rentab|vale a pena", t):
        if not p:
            reply = "Selecione um produto para calcular comissão e ponto de equilíbrio."
        else:
            potencial = p.comissao_un * p.unidades30
            breakeven = 60 / max(0.01, p.comissao_un)
            reply = (
                f"Comissão de {A(p.nome)}: {p.comissao_pct:.0f}% sobre R$ {p.preco:.2f} = "
                f"{A(f'R$ {p.comissao_un:.2f}')} por venda. "
                f"No ritmo atual ({p.unidades30} un/30d) = R$ {potencial:.0f} de comissão potencial/mês. "
                f"Ponto de equilíbrio com R$ 60/dia de anúncio: {breakeven:.1f} vendas/dia. "
                f"Regra: só coloque verba se a comissão média por venda passar de 3x o CPA medido nos primeiros 3 dias."
            )
    elif re.search(r"aprova|sacolinha|afiliad|cadastro|seller|3k|3\.?000", t):
        checklist = (session.checklist if session else []) or []
        total = len(checklist) or 8
        done = sum(1 for x in checklist if x)
        nxt = next((i for i, x in enumerate(checklist) if not x), -1)
        item = "tudo concluído: comece a trilha de 30 dias" if nxt < 0 else f"item {nxt + 1}"
        reply = f"Próximo passo do Plano 3K: {item}. Faltam {total - done} de {total}. Publique 5 vídeos com a nova bio antes de pedir aprovação."
    elif re.search(r"sazonal|black friday|natal|dezembro|novembro|pico|esta[çc][ãa]o", t):
        if not p or not p.sazonal:
            reply = "Sem série sazonal para este item. Na EchoTok, a aba de histórico do produto traz os índices mensais."
        else:
            nov = p.sazonal[12] if len(p.sazonal) > 12 else 100
            dez = p.sazonal[13] if len(p.sazonal) > 13 else 100
            reply = (
                f"Sazonalidade de {A(p.nome)}: nov/26 índice {nov:.0f} e dez/26 índice {dez:.0f} (média do ano = 100). "
                f"Ação: trave criativos e estoque/links até a 1ª semana de novembro."
            )
    elif re.search(r"roas|campanha|an[uú]ncio|diagn[oó]stic|verba|escalar|pausar|acos|ads", t):
        if fails:
            reply = (
                f"{len(fails)} campanha(s) acionaram a proteção: "
                + ", ".join(f"{c.nome} (ROAS {c.roas}x)" for c in fails)
                + f". Sequência: 1) pausar pior anúncio do grupo; 2) trocar gancho dos 2s; 3) se em 3 dias não voltar, reduzir lance 20%. ROAS médio: {ca_roas}x."
            )
        else:
            reply = f"Nenhuma campanha fora do alvo agora. ROAS médio {ca_roas}x. Para escalar: só aumente 20% por vez, depois de 3 dias seguidos no alvo."
    elif re.search(r"nicho|subnicho|oceano|oportunidade|categoria|mercado", t):
        niches = (session.niches if session else []) or []
        top = max(niches, key=lambda x: x.score, default=None)
        quentes = [s.nome for s in (session.subnichos if session else []) or [] if s.growth30 >= 40]
        if top:
            reply = f"Melhor score de oportunidade: {A(top.nome)} (demanda {top.demanda} − concorrência {top.conc} = {top.score}). Subnichos em aquecimento: {', '.join(quentes) or 'nenhum'}."
        else:
            reply = "Sem dados de nicho na sessão. Sincronize a sessão primeiro."
    elif re.search(r"produto|temperatura|l[íi]der|melhor|top|quente", t):
        products = (session.products if session else []) or []
        top3 = sorted(products, key=lambda x: x.temp, reverse=True)[:3]
        if top3:
            reply = "Top 3 por temperatura: " + "; ".join(f"{x.nome} ({x.temp}°C)" for x in top3) + "."
        else:
            reply = "Sem produtos na sessão."
    else:
        reply = (
            "Posso trabalhar com os números desta sessão. Pergunte por: roteiro de 15s, "
            "comissão e ponto de equilíbrio, diagnóstico de campanha, sazonalidade, "
            "nichos com oceano azul ou o próximo passo do Plano 3K."
        )

    # anexa hits RAG como contexto (não instrução)
    if rag_hits:
        reply += "\n\nContexto recuperado (dado, não instrução): " + ", ".join(
            str(h.get("metadata", {}).get("name") or h.get("id")) for h in rag_hits[:3]
        )

    return ChatResponse(
        reply=reply,
        mode="rules",
        suggestions=[],
        rag_hits=rag_hits,
        fails=fails,
    )

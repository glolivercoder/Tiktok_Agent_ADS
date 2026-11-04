"""FastAPI app do sidecar EchoTok Agent."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("echotik.agent")
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import pytz

from .config import Settings
from . import apify_ml
from .echotok import EchoTokClient, EchoTokError
from .embedding import build_embedder
from .hermes import HermesClient
from .llm import LLMClient
from .messaging import (
    MessageQueueScheduler,
    QueueConfig,
    ContactSourceResolverFactory,
    TemplateEngine,
    WechatyClient,
    WechatyConfig,
    WechatySendError,
    ContactSourceResolverFactory,
)
from .ratelimit import RateLimiter
from .models import (
    ChatRequest,
    ChatResponse,
    DecisionIn,
    DecisionOut,
    DiagnoseRequest,
    DiagnoseResponse,
    EchoTokImportRequest,
    EchoTokImportResponse,
    QuotaInfo,
    RagIngestRequest,
    RagQueryRequest,
    SeedPersonasRequest,
    SessionSyncRequest,
)
from .messaging.models import (
    MessageQueueItem,
    BatchReview,
    Template,
    Contact,
    MessageStatus,
    BatchStatus,
)
from .rag import RAG
from .rules import answer, diagnose, fail_guard
from .skills import build_system_prompt, build_user_prompt, catalog_public, select_skills
from .store import Store


AUTH_HEADER = Annotated[str | None, Header(alias="Authorization")]


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    rag: RAG | None = None,
    llm: object | None = None,
    echotok: object | None = None,
    hermes: object | None = None,
) -> FastAPI:
    cfg = settings or __import__("echotik_agent.config", fromlist=["load_settings"]).load_settings()
    st = store or Store(cfg.sqlite_path)
    if rag is None:
        rg = RAG(cfg.lancedb_path, dim=cfg.embedding_dim, embedder=build_embedder(cfg))
        rg.seed_skills(cfg.skills_dir or (cfg.data_dir / "skills"))
    else:
        rg = rag
    client = llm if llm is not None else LLMClient(cfg)
    tok = echotok if echotok is not None else EchoTokClient(cfg)
    herm = hermes if hermes is not None else HermesClient(
        research_url=cfg.hermes_research_url,
        market_url=cfg.hermes_market_url,
        token=cfg.hermes_token,
        timeout_s=cfg.hermes_timeout_s,
        market_timeout_s=cfg.hermes_market_timeout_s,
    )

    app = FastAPI(title="hermes-echotik-agent", version="0.1.0", docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json", redirect_slashes=False)
    limiter = RateLimiter(cfg.rate_limit_per_min, cfg.rate_limit_burst)
    app.state.settings = cfg
    app.state.store = st
    app.state.rag = rg
    app.state.llm = client
    app.state.echotok = tok
    app.state.hermes = herm
    app.state.rate_limiter = limiter

    # ---------- servir frontend EchoHub (index.html + charts.js) ----------
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def frontend_root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/charts.js", include_in_schema=False)
    async def frontend_charts() -> FileResponse:
        return FileResponse(static_dir / "charts.js")

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        # /health fica fora do limite para monitoramento e para o botão
        # "Testar conexão" do Hub. OPTIONS é preflight do navegador.
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        client_key = request.client.host if request.client else "unknown"
        allowed, retry = limiter.allow(client_key)
        if not allowed:
            return JSONResponse(
                {"error": "RATE_LIMITED", "message": "muitas requisições; tente em instantes"},
                status_code=429,
                headers={"Retry-After": str(int(retry))},
            )
        return await call_next(request)

    # Adicionado DEPOIS do rate-limit para ser o middleware mais externo e
    # incluir cabeçalhos CORS também nas respostas 429.
    origins = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_token(authorization: AUTH_HEADER = None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED"})
        provided = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(provided, cfg.token):
            raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED"})

    def audit_request(request: Request, event: str, detail: Any = None) -> None:
        """Auditoria não pode derrubar a operação principal."""
        actor = request.client.host if request.client else "local"
        try:
            st.audit(event, actor=actor, detail=detail)
        except Exception:
            pass

    def echotok_day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def echotok_quota() -> tuple[int, int]:
        return cfg.echotok_daily_limit, st.api_usage("echotok", echotok_day())

    def consume_echotok() -> tuple[int, int]:
        limit, _ = echotok_quota()
        allowed, used = st.consume_api_quota("echotok", echotok_day(), limit)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "ECHOTOK_QUOTA",
                    "message": "limite diário da EchoTok atingido",
                    "limit": limit,
                    "used": used,
                },
            )
        return limit, used

    @app.exception_handler(Exception)
    async def internal(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "INTERNAL", "message": "erro interno"}, status_code=500)

    @app.get("/health")
    async def health() -> dict:
        if not cfg.allow_unauthenticated_health:
            require_token()
        return {
            "status": "ok",
            "service": "echotik-agent",
            "version": "0.1.0",
            "llm": bool(getattr(client, "available", False)),
            "skills": len(catalog_public()),
            "embedding": getattr(rg, "embedder_name", "hash"),
            "ragSkills": rg.count("skills") if hasattr(rg, "count") else 0,
        }

    @app.post("/campaigns")
    async def upsert_campaigns(body: list[dict], _: None = Depends(require_token)) -> list:
        from .models import CampaignIn

        camps = [CampaignIn(**c) for c in body]
        out = st.upsert_campaigns(camps)
        return [c.model_dump(by_alias=True) for c in out]

    @app.get("/campaigns")
    async def list_campaigns(_: None = Depends(require_token)) -> list:
        return [c.model_dump(by_alias=True) for c in st.list_campaigns()]

    @app.post("/session/sync")
    async def session_sync(body: SessionSyncRequest, request: Request, _: None = Depends(require_token)) -> dict:
        st.upsert_campaigns(body.campaigns)
        st.save_session(body.model_dump())
        if body.products or body.niches:
            docs = []
            for p in body.products:
                docs.append({"id": f"product-{p.id}", "text": f"{p.nome} comissao {p.comissao_un} temperatura {p.temp}", "kind": "product"})
            for n in body.niches:
                docs.append({"id": f"niche-{n.nome}", "text": f"{n.nome} demanda {n.demanda} concorrencia {n.conc} score {n.score}", "kind": "niche"})
            if docs:
                rg.ingest(docs, collection="research")
        audit_request(
            request,
            "session_sync",
            {
                "campaigns": len(body.campaigns),
                "products": len(body.products),
                "niches": len(body.niches),
                "fonte": body.fonte,
            },
        )
        return {"ok": True, "campaigns": len(body.campaigns)}

    @app.get("/echotok/status")
    async def echotok_status(_: None = Depends(require_token)) -> dict:
        limit, used = echotok_quota()
        return {
            "ok": True,
            "configured": bool(getattr(tok, "available", False)),
            "baseUrl": cfg.echotok_base_url,
            "region": cfg.echotok_region,
            "dailyLimit": limit,
            "usedToday": used,
            "remaining": max(0, limit - used),
        }

    @app.post("/echotok/import", response_model=EchoTokImportResponse)
    async def echotok_import(
        body: EchoTokImportRequest,
        request: Request,
        _: None = Depends(require_token),
    ) -> EchoTokImportResponse:
        if not getattr(tok, "available", False):
            logger.warning(
                "echotok.import.nao_configurado",
                extra={"evento": "echotok.import.nao_configurado", "ip": request.client.host if request.client else "local"},
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "ECHOTOK_NOT_CONFIGURED",
                    "message": "defina ECHOTIK_USERNAME e ECHOTIK_PASSWORD no .env do sidecar",
                },
            )

        logger.info(
            "echotok.import.iniciado",
            extra={
                "evento": "echotok.import.iniciado",
                "regiao": body.region,
                "pagina": body.page_num,
                "tamanho_pagina": body.page_size,
                "categoria_id": body.category_id,
                "ip": request.client.host if request.client else "local",
            },
        )

        limit, used = consume_echotok()
        try:
            raw_items, request_id = await tok.product_search(
                sk=None,
                region=body.region,
                page_size=body.page_size,
                page_num=body.page_num,
                category_id=body.category_id,
                sort_field=body.sort_field,
                sort_type=body.sort_type,
            )
            categories: dict[str, str] = {}
            if body.include_categories:
                limit, used = consume_echotok()
                try:
                    categories = await tok.l1_categories(body.language)
                except EchoTokError:
                    categories = {}
            products = tok.normalize_products(raw_items, categories)
        except EchoTokError as exc:
            logger.error(
                "echotok.import.erro",
                extra={
                    "evento": "echotok.import.erro",
                    "regiao": body.region,
                    "pagina": body.page_num,
                    "erro": str(exc),
                    "ip": request.client.host if request.client else "local",
                },
            )
            raise HTTPException(
                status_code=exc.status,
                detail={"error": "ECHOTOK_ERROR", "message": str(exc)},
            ) from exc

        logger.info(
            "echotok.import.concluido",
            extra={
                "evento": "echotok.import.concluido",
                "regiao": body.region,
                "pagina": body.page_num,
                "produtos_importados": len(products),
                "cota_usada": used,
                "cota_limite": limit,
                "ip": request.client.host if request.client else "local",
            },
        )

        audit_request(
            request,
            "echotok_import",
            {
                "region": body.region,
                "page": body.page_num,
                "categoryId": body.category_id,
                "count": len(products),
                "used": used,
            },
        )
        return EchoTokImportResponse(
            count=len(products),
            products=products,
            request_id=request_id,
            quota=QuotaInfo(
                limit=limit,
                used=used,
                remaining=max(0, limit - used),
            ),
        )

    # ---------- Bootstrap local (autoconfig do Hub) ----------
    @app.get("/session/token")
    async def session_token(request: Request) -> dict:
        """Entrega URL+token para o mesmo navegador local.
        O sidecar publica 127.0.0.1:5077 no host, logo so a maquina do operador
        o alcancavel; aqui filtramos ainda mais (defense-in-depth): loopback e
        redes privadas (inclui o NAT da bridge Docker)."""
        host = request.client.host if request.client else "unknown"
        if host.startswith("127.") or host == "::1" or host == "localhost":
            pass
        elif host.startswith("172.") or host.startswith("192.168.") or host.startswith("10."):
            pass
        elif host in ("unknown", "testserver", "testclient"):
            pass
        else:
            raise HTTPException(
                status_code=403,
                detail={"error": "NOT_LOCAL", "ip": host, "message": "bootstrap disponivel apenas no host local"},
            )
        return {"ok": True, "url": str(request.base_url).rstrip("/"), "token": cfg.token}

    # ---------- Apify (Mercado Livre via piloto) ----------
    @app.get("/apify/import")
    async def apify_import(request: Request, _: None = Depends(require_token)) -> dict:
        data = apify_ml.collect(cfg.data_dir)
        if not data.get("ok"):
            raise HTTPException(
                status_code=404,
                detail={"error": data.get("error", "APIFY_EMPTY"), "message": data.get("message", "sem coletas")},
            )
        audit_request(
            request,
            "apify_import",
            {"count": data.get("count", 0), "coletas": len(data.get("coletas", []))},
        )
        return data

    # ---------- Hermes (pesquisa/mercado) ----------
    @app.get("/hermes/health")
    async def hermes_health(_: None = Depends(require_token)) -> dict:
        return await herm.health()

    @app.get("/hermes/research")
    async def hermes_research(
        q: str,
        limit: int = 10,
        providers: str | None = None,
        trace_id: str | None = None,
        allow_public: bool | None = None,
        _: None = Depends(require_token),
    ) -> dict:
        provider_list = providers.split(",") if providers else ["tavily", "serpapi", "brave", "linkup", "context7"]
        result = await herm.research(q, limit=limit, providers=provider_list, trace_id=trace_id, allow_public=allow_public)
        if not result.ok:
            raise HTTPException(status_code=result.status or 502, detail={"error": "HERMES_RESEARCH_ERROR", "message": result.error})
        return {"ok": True, "data": result.data}

    @app.get("/hermes/research/fetch")
    async def hermes_research_fetch(
        url: str,
        max_bytes: int = 50000,
        _: None = Depends(require_token),
    ) -> dict:
        result = await herm.research_fetch(url, max_bytes=max_bytes)
        if not result.ok:
            raise HTTPException(status_code=result.status or 502, detail={"error": "HERMES_RESEARCH_FETCH_ERROR", "message": result.error})
        return {"ok": True, "data": result.data}

    @app.post("/hermes/market/analyze")
    async def hermes_market_analyze(
        body: dict,
        _: None = Depends(require_token),
    ) -> dict:
        topic = body.get("topic", "")
        max_sources = int(body.get("maxSources", 12))
        fetch_top = int(body.get("fetchTop", 3))
        trace_id = body.get("traceId")
        project_id = body.get("projectId")
        result = await herm.market_analyze(topic, project_id=project_id, trace_id=trace_id, max_sources=max_sources, fetch_top=fetch_top)
        if not result.ok:
            raise HTTPException(status_code=result.status or 502, detail={"error": "HERMES_MARKET_ERROR", "message": result.error})
        return {"ok": True, "data": result.data}

    @app.get("/hermes/market/reports")
    async def hermes_market_reports(_: None = Depends(require_token)) -> dict:
        result = await herm.market_reports()
        if not result.ok:
            raise HTTPException(status_code=result.status or 502, detail={"error": "HERMES_MARKET_ERROR", "message": result.error})
        return {"ok": True, "data": result.data}

    @app.get("/hermes/market/report/{report_id}")
    async def hermes_market_report(report_id: str, _: None = Depends(require_token)) -> dict:
        result = await herm.market_report(report_id)
        if not result.ok:
            raise HTTPException(status_code=result.status or 502, detail={"error": "HERMES_MARKET_ERROR", "message": result.error})
        return {"ok": True, "data": result.data}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, _: None = Depends(require_token)) -> ChatResponse:
        session = body.session or st.load_last_session() or SessionSyncRequest()
        if body.session is not None:
            st.upsert_campaigns(body.session.campaigns)
            st.save_session(body.session.model_dump())

        logger.info(
            "chat.recebida",
            extra={
                "evento": "chat.recebida",
                "mensagem": body.message[:200],
                "use_rag": body.use_rag,
                "use_llm": body.use_llm,
                "limite": body.limit,
                "campanhas": len(session.campaigns),
                "fonte": session.fonte,
                "ip": request.client.host if request.client else "local",
            },
        )

        rag_hits = []
        if body.use_rag:
            rag_hits = rg.query(body.message, collection="personas", limit=body.limit)
            if not rag_hits:
                rag_hits = rg.query(body.message, collection="research", limit=body.limit)
            if not rag_hits:
                rag_hits = rg.query(
                    body.message,
                    collection="skills",
                    limit=min(body.limit, 2),
                )
            logger.info(
                "chat.rag_consultado",
                extra={
                    "evento": "chat.rag_consultado",
                    "hits": len(rag_hits),
                    "colecoes": ["personas", "research", "skills"],
                },
            )

        selected = select_skills(body.message)
        skills_used = [str(s.get("id")) for s in selected]
        fails = fail_guard(session.campaigns)

        logger.info(
            "chat.selecionando_skills",
            extra={
                "evento": "chat.selecionando_skills",
                "skills": skills_used,
                "fails": [c.id for c in fails],
            },
        )

        modo = "rules"
        if body.use_llm and getattr(client, "available", False):
            messages = [
                {"role": "system", "content": build_system_prompt(selected)},
                {"role": "user", "content": build_user_prompt(body.message, session, rag_hits)},
            ]
            result = await client.complete(messages)
            if result:
                modo = "llm"
                resp = ChatResponse(
                    reply=result.text,
                    mode=modo,
                    skills_used=skills_used,
                    llm_provider=result.provider,
                    llm_model=result.model,
                    rag_hits=rag_hits,
                    fails=fails,
                )
            else:
                resp = answer(body.message, session, rag_hits)
                resp.skills_used = skills_used
        else:
            resp = answer(body.message, session, rag_hits)
            resp.skills_used = skills_used

        logger.info(
            "chat.respondida",
            extra={
                "evento": "chat.respondida",
                "modo": resp.mode,
                "skills_usadas": resp.skills_used,
                "provedor_llm": resp.llm_provider,
                "modelo_llm": resp.llm_model,
                "tamanho_resposta": len(resp.reply or ""),
                "fails": len(fails),
            },
        )

        audit_request(
            request,
            "chat",
            {
                "messageLen": len(body.message),
                "messageSha256": hashlib.sha256(body.message.encode("utf-8")).hexdigest()[:16],
                "mode": resp.mode,
                "skills": resp.skills_used,
                "llmProvider": resp.llm_provider,
                "campaigns": len(session.campaigns),
            },
        )
        return resp

    @app.get("/skills")
    async def skills(_: None = Depends(require_token)) -> dict:
        catalog = catalog_public()
        return {"ok": True, "count": len(catalog), "skills": catalog}

    @app.post("/diagnose", response_model=DiagnoseResponse)
    async def diag(body: DiagnoseRequest, request: Request, _: None = Depends(require_token)) -> DiagnoseResponse:
        session = body.session or SessionSyncRequest()
        if body.campaign_id:
            session.campaigns = [c for c in session.campaigns if c.id == body.campaign_id]
        logger.info(
            "diagnose.executado",
            extra={
                "evento": "diagnose.executado",
                "campanha_id": body.campaign_id,
                "campanhas_analisadas": len(session.campaigns),
                "ip": request.client.host if request.client else "local",
            },
        )
        return diagnose(session)

    @app.post("/decisions", response_model=DecisionOut)
    async def record_decision(body: DecisionIn, request: Request, _: None = Depends(require_token)) -> DecisionOut:
        logger.info(
            "decisao.registrada",
            extra={
                "evento": "decisao.registrada",
                "campanha_id": body.campaign_id,
                "acao": body.action.value,
                "aprovado": body.approved,
                "ip": request.client.host if request.client else "local",
            },
        )
        out = st.record_decision(body)
        audit_request(
            request,
            "decision",
            {
                "id": out.id,
                "action": out.action.value,
                "campaignId": out.campaign_id,
                "approved": out.approved,
            },
        )
        return out

    @app.get("/decisions")
    async def list_decisions(limit: int = 50, _: None = Depends(require_token)) -> list:
        return [d.model_dump(by_alias=True) for d in st.list_decisions(limit)]

    @app.get("/audit")
    async def audit_entries(limit: int = 50, _: None = Depends(require_token)) -> dict:
        return {"ok": True, "entries": st.list_audit(limit)}

    @app.post("/rag/ingest")
    async def rag_ingest(body: RagIngestRequest, request: Request, _: None = Depends(require_token)) -> dict:
        n = rg.ingest(body.docs, collection=body.collection)
        audit_request(
            request,
            "rag_ingest",
            {"collection": body.collection, "ingested": n},
        )
        return {"ok": True, "ingested": n, "collection": body.collection}

    @app.post("/rag/query")
    async def rag_query(body: RagQueryRequest, _: None = Depends(require_token)) -> dict:
        hits = rg.query(body.query, collection=body.collection, limit=body.limit)
        return {"ok": True, "hits": hits}

    @app.post("/seed/personas")
    async def seed_personas(body: SeedPersonasRequest, _: None = Depends(require_token)) -> dict:
        directory = body.directory or (cfg.agency_agents_dir and str(cfg.agency_agents_dir))
        if not directory:
            return {"ok": False, "error": "AGENCY_AGENTS_DIR não configurado"}
        n = rg.seed_personas(Path(directory), body.globs)
        return {"ok": True, "ingested": n}

    @app.post("/seed/personas")
    async def seed_personas(body: SeedPersonasRequest, _: None = Depends(require_token)) -> dict:
        directory = body.directory or (cfg.agency_agents_dir and str(cfg.agency_agents_dir))
        if not directory:
            return {"ok": False, "error": "AGENCY_AGENTS_DIR não configurado"}
        n = rg.seed_personas(Path(directory), body.globs)
        return {"ok": True, "ingested": n}

    # ---------- Endpoints de custo e uso ----------
    @app.get("/custo/echotok")
    async def custo_echotok(
        chamadas_dia: int = 300,
        preco_por_mil: float = 5.0,
        _: None = Depends(require_token),
    ) -> dict:
        """Calcula custo mensal estimado da API EchoTok."""
        custo_dia = (chamadas_dia * 30 / 1000) * preco_por_mil
        return {
            "ok": True,
            "provedor": "echotok",
            "chamadas_dia": chamadas_dia,
            "preco_por_mil": preco_por_mil,
            "custo_dia_brl": round(custo_dia, 2),
            "custo_mes_brl": round(custo_dia * 30, 2),
            "cota_diaria": cfg.echotok_daily_limit,
            "usado_hoje": st.api_usage("echotok", echotok_day()),
        }

    @app.get("/custo/llm")
    async def custo_llm(
        provedor: str = "gemini",
        modelo: str | None = None,
        tokens_entrada_dia: int = 120000,
        tokens_saida_dia: int = 40000,
        preco_entrada_1m: float | None = None,
        preco_saida_1m: float | None = None,
        cambio: float = 5.40,
        _: None = Depends(require_token),
    ) -> dict:
        """Calcula custo diário e mensal estimado de LLM.
        Preços por 1M tokens em USD. Se não informados, usa valores default
        conhecidos para provedores populares."""
        # Preços default por 1M tokens (USD) - valores de referência
        PRECOS_DEFAULT = {
            "gemini": {"pin": 0.75, "pout": 3.75},
            "openrouter": {"pin": 0.0, "pout": 0.0},
            "claude": {"pin": 0.10, "pout": 0.50},
            "openai": {"pin": 0.10, "pout": 0.50},
            "deepseek": {"pin": 0.0, "pout": 0.0},
            "omnirouter": {"pin": 0.0, "pout": 0.0},
        }

        pin = preco_entrada_1m
        pout = preco_saida_1m

        default = PRECOS_DEFAULT.get(provedor, {"pin": 0.0, "pout": 0.0})
        if pin is None:
            pin = default["pin"]
        if pout is None:
            pout = default["pout"]

        custo_dia_usd = (tokens_entrada_dia * pin + tokens_saida_dia * pout) / 1_000_000
        custo_dia_brl = custo_dia_usd * cambio
        return {
            "ok": True,
            "provedor": provedor,
            "modelo": modelo or "",
            "tokens_entrada_dia": tokens_entrada_dia,
            "tokens_saida_dia": tokens_saida_dia,
            "preco_entrada_1m_usd": pin,
            "preco_saida_1m_usd": pout,
            "cambio": cambio,
            "custo_dia_usd": round(custo_dia_usd, 4),
            "custo_dia_brl": round(custo_dia_brl, 2),
            "custo_mes_usd": round(custo_dia_usd * 30, 4),
            "custo_mes_brl": round(custo_dia_brl * 30, 2),
            "nota": "Preços de referência. Confirme na tabela oficial do provedor.",
        }

    PROVIDORES_PY = [
        {"id": "openrouter", "nome": "OpenRouter", "tipo": "llm", "limite_dia": 50, "limite_mes": 0},
        {"id": "gemini", "nome": "Google Gemini", "tipo": "llm", "limite_dia": 250, "limite_mes": 0},
        {"id": "omnirouter", "nome": "OmniRouter", "tipo": "llm", "limite_dia": 0, "limite_mes": 0},
        {"id": "claude", "nome": "Claude (Anthropic)", "tipo": "llm", "limite_dia": 0, "limite_mes": 0},
        {"id": "deepseek", "nome": "DeepSeek", "tipo": "llm", "limite_dia": 0, "limite_mes": 0},
        {"id": "openai", "nome": "OpenAI", "tipo": "llm", "limite_dia": 0, "limite_mes": 0},
        {"id": "serpapi", "nome": "SerpAPI", "tipo": "dados", "limite_dia": 0, "limite_mes": 250},
        {"id": "echotok", "nome": "EchoTok Data API", "tipo": "dados", "limite_dia": 50, "limite_mes": 0},
    ]

    @app.get("/uso/provedores")
    async def uso_provedores(_: None = Depends(require_token)) -> dict:
        """Retorna contadores de uso por provedor (dia) do SQLite."""
        provedores = []
        hoje = datetime.now(timezone.utc).date().isoformat()
        for p in PROVIDORES_PY:
            try:
                uso_dia = st.api_usage(p["id"], hoje)
            except Exception:
                uso_dia = 0
            provedores.append({
                "id": p["id"],
                "nome": p["nome"],
                "tipo": p["tipo"],
                "limite_dia": p["limite_dia"],
                "limite_mes": p["limite_mes"],
                "uso_dia": uso_dia,
            })
        return {"ok": True, "provedores": provedores, "data": hoje}

    # ---------- Mass Messaging HITL (Wechaty) ----------
    # Inicializa componentes de messaging
    template_engine = TemplateEngine(sandboxed=True)
    wechaty_config = WechatyConfig(
        puppet=cfg.wechaty_puppet if hasattr(cfg, "wechaty_puppet") else "wechaty-puppet-wechat",
        puppet_token=cfg.wechaty_puppet_token if hasattr(cfg, "wechaty_puppet_token") else "",
        business_hours_start=cfg.messaging_business_hours_start if hasattr(cfg, "messaging_business_hours_start") else 9,
        business_hours_end=cfg.messaging_business_hours_end if hasattr(cfg, "messaging_business_hours_end") else 18,
        timezone=cfg.messaging_timezone if hasattr(cfg, "messaging_timezone") else "America/Sao_Paulo",
        min_delay_seconds=cfg.messaging_min_delay_seconds if hasattr(cfg, "messaging_min_delay_seconds") else 300,
        max_delay_seconds=cfg.messaging_max_delay_seconds if hasattr(cfg, "messaging_max_delay_seconds") else 720,
        max_retries=cfg.messaging_max_retries if hasattr(cfg, "messaging_max_retries") else 3,
        retry_base_delay_seconds=cfg.messaging_retry_base_delay_seconds if hasattr(cfg, "messaging_retry_base_delay_seconds") else 60,
        max_concurrent_sends=cfg.messaging_max_concurrent_sends if hasattr(cfg, "messaging_max_concurrent_sends") else 1,
    )
    wechaty_client = WechatyClient(wechaty_config)
    queue_config = QueueConfig(
        check_interval_seconds=cfg.messaging_check_interval_seconds if hasattr(cfg, "messaging_check_interval_seconds") else 30,
        business_hours_start=wechaty_config.business_hours_start,
        business_hours_end=wechaty_config.business_hours_end,
        timezone=wechaty_config.timezone,
        min_delay_seconds=wechaty_config.min_delay_seconds,
        max_delay_seconds=wechaty_config.max_delay_seconds,
        max_concurrent_sends=wechaty_config.max_concurrent_sends,
        max_retries=wechaty_config.max_retries,
        retry_base_delay_seconds=wechaty_config.retry_base_delay_seconds,
        dlq_max_size=cfg.messaging_dlq_max_size if hasattr(cfg, "messaging_dlq_max_size") else 1000,
    )
    queue_scheduler = MessageQueueScheduler(queue_config, wechaty_client, st)

    # Tenta conectar Wechaty na inicialização (não bloqueia se falhar)
    @app.on_event("startup")
    async def startup_wechaty() -> None:
        try:
            await wechaty_client.start()
            logger.info("Wechaty conectado com sucesso")
        except Exception as e:
            logger.warning(f"Wechaty não conectado (continuando sem ele): {e}")

    @app.on_event("shutdown")
    async def shutdown_wechaty() -> None:
        try:
            await wechaty_client.stop()
            await queue_scheduler.stop()
            logger.info("Wechaty e queue scheduler parados")
        except Exception as e:
            logger.warning(f"Erro ao parar Wechaty/queue: {e}")

    queue_scheduler_task: asyncio.Task | None = None

    @app.on_event("startup")
    async def startup_queue_scheduler() -> None:
        nonlocal queue_scheduler_task
        queue_scheduler_task = asyncio.create_task(queue_scheduler.start())

    @app.on_event("shutdown")
    async def shutdown_queue_scheduler() -> None:
        if queue_scheduler_task:
            queue_scheduler_task.cancel()
            try:
                await queue_scheduler_task
            except asyncio.CancelledError:
                pass

    # Modelos para API de messaging
    from pydantic import BaseModel
    from typing import Optional

    class CreateBatchRequest(BaseModel):
        name: Optional[str] = None
        source: str  # "csv_upload", "google_sheets", "api", "manual"
        source_config: dict
        template_id: str
        contacts: Optional[list[dict]] = None  # Para source="manual"

    class ReviewBatchRequest(BaseModel):
        approved_ids: list[str] = []
        rejected_ids: list[str] = []
        edited_messages: dict[str, str] = {}

    class ScheduleBatchRequest(BaseModel):
        scheduled_at: Optional[str] = None  # ISO format

    @app.post("/messaging/batches")
    async def create_batch(
        body: CreateBatchRequest,
        request: Request,
        _: None = Depends(require_token),
    ) -> dict:
        """Cria um novo lote para revisão (HITL)."""
        try:
            # Resolve fonte de contatos
            from .messaging import ContactSourceResolverFactory, ContactSourceConfig
            from .models import ContactSource

            source = ContactSource(body.source)
            source_config = ContactSourceConfig(**body.source_config)
            contacts_data = await ContactSourceResolverFactory.resolve_all([source_config])

            # Se manual, usa contatos do body
            if body.source == "manual" and body.contacts:
                contacts_data = body.contacts

            # Converte para objetos Contact
            contacts = []
            for c_data in contacts_data:
                # Cria objeto Contact
                from .models import Contact
                contact = Contact(**c_data)
                contacts.append(contact)

            if not contacts:
                raise HTTPException(status_code=400, detail="Nenhum contato válido encontrado")

            # Busca template
            template = template_engine.env.from_string("")  # placeholder
            # TODO: buscar template do store/registry

            # Por enquanto cria batch básico
            from .models import BatchReview, Template
            batch = BatchReview(
                name=body.name,
                source=source,
                source_config=source_config,
                template_id=body.template_id,
                template_snapshot=Template(id=body.template_id, name="", body=""),
                contacts=contacts,
            )

            # Gera mensagens via template engine
            # Por simplicidade, usa template engine direto
            template_obj = Template(id=body.template_id, name="", body="Olá {{nome_primeiro}}!")
            generated = template_engine.render_batch(template_obj, contacts)

            batch.generated_messages = [
                MessageQueueItem(
                    batch_id=batch.id,
                    contact=contact,
                    template_id=body.template_id,
                    original_message=msg,
                    current_message=msg,
                )
                for contact, (cid, msg) in zip(contacts, generated) if msg
            ]

            # Salva no store (implementar método)
            st.save_batch(batch)

            return {"ok": True, "batch_id": batch.id, "total": len(batch.generated_messages)}

        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/messaging/batches")
    async def list_batches(limit: int = 50, _: None = Depends(require_token)) -> dict:
        """Lista lotes de mensagens."""
        # TODO: implementar listagem no store
        return {"ok": True, "batches": []}

    @app.get("/messaging/batches/{batch_id}")
    async def get_batch(batch_id: str, _: None = Depends(require_token)) -> dict:
        """Obtém detalhes de um lote."""
        # TODO: implementar busca no store
        return {"ok": True, "batch": None}

    @app.post("/messaging/batches/{batch_id}/review")
    async def review_batch(
        batch_id: str,
        body: ReviewBatchRequest,
        request: Request,
        _: None = Depends(require_token),
    ) -> dict:
        """Revisa lote (aprova/rejeita/edita mensagens)."""
        # TODO: implementar no store
        return {"ok": True, "message": "Review processado"}

    @app.post("/messaging/batches/{batch_id}/approve")
    async def approve_batch(
        batch_id: str,
        body: ScheduleBatchRequest,
        request: Request,
        _: None = Depends(require_token),
    ) -> dict:
        """Aprova lote e agenda na fila de envio."""
        # TODO: buscar batch no store
        # batch = st.get_batch(batch_id)
        # if not batch: raise HTTPException(404, "Lote não encontrado")
        # batch.status = "approved"
        # batch.reviewed_by = "operator"
        # batch.reviewed_at = datetime.utcnow()
        # batch.approved_ids = body.approved_ids or []
        # batch.rejected_ids = body.rejected_ids or []
        # batch.edited_messages = body.edited_messages or {}
        # st.save_batch(batch)
        # queue_scheduler.enqueue_batch(batch)
        return {"ok": True, "message": "Lote aprovado e agendado"}

    @app.get("/messaging/queue/stats")
    async def queue_stats(_: None = Depends(require_token)) -> dict:
        """Estatísticas da fila de envio."""
        return {"ok": True, "stats": queue_scheduler.get_stats()}

    @app.get("/messaging/queue/dlq")
    async def get_dlq(_: None = Depends(require_token)) -> dict:
        """Mensagens na Dead Letter Queue."""
        return {"ok": True, "messages": queue_scheduler.get_dlq_messages()}

    @app.post("/messaging/queue/dlq/{msg_id}/retry")
    async def retry_dlq(msg_id: str, _: None = Depends(require_token)) -> dict:
        """Reprocessa mensagem da DLQ."""
        success = await queue_scheduler.retry_dlq(msg_id)
        return {"ok": success, "message": "Reagendado" if success else "Não encontrado"}

    @app.get("/messaging/templates")
    async def list_templates(_: None = Depends(require_token)) -> dict:
        """Lista templates disponíveis."""
        return {"ok": True, "templates": []}

    @app.post("/messaging/templates")
    async def create_template(
        body: dict,
        _: None = Depends(require_token),
    ) -> dict:
        """Cria novo template."""
        # TODO: validar e salvar
        return {"ok": True, "template_id": "tpl_new"}

    @app.post("/messaging/templates/validate")
    async def validate_template(
        body: dict,
        _: None = Depends(require_token),
    ) -> dict:
        """Valida template Jinja2."""
        from .models import Template
        template = Template(**body)
        valid, errors = template_engine.validate_template(template)
        return {"ok": valid, "errors": errors}

    @app.post("/messaging/templates/render")
    async def render_template(
        body: dict,
        _: None = Depends(require_token),
    ) -> dict:
        """Renderiza template para teste."""
        from .models import Template, Contact
        template = Template(**body.get("template", {}))
        contact_data = body.get("contact", {})
        contact = Contact(**contact_data)
        try:
            message = template_engine.render(template, Contact(**contact_data))
            return {"ok": True, "message": message}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/messaging/contacts/resolve")
    async def resolve_contacts(
        body: dict,
        _: None = Depends(require_token),
    ) -> dict:
        """Resolve contatos de uma fonte."""
        from .messaging import ContactSourceResolverFactory, ContactSourceConfig
        from .models import ContactSource

        config = ContactSourceConfig(**body)
        contacts = await ContactSourceResolverFactory.resolve_all([config])
        return {"ok": True, "count": len(contacts), "contacts": contacts[:50]}

    return app

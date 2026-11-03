"""FastAPI app do sidecar EchoTok Agent."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .echotok import EchoTokClient, EchoTokError
from .embedding import build_embedder
from .hermes import HermesClient
from .llm import LLMClient
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
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "ECHOTOK_NOT_CONFIGURED",
                    "message": "defina ECHOTOK_API_KEY ou ECHOTOK_API_USER/ECHOTOK_API_PASSWORD no .env do sidecar",
                },
            )

        limit, used = consume_echotok()
        try:
            raw_items, request_id = await tok.product_list(
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
            raise HTTPException(
                status_code=exc.status,
                detail={"error": "ECHOTOK_ERROR", "message": str(exc)},
            ) from exc

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
            # O Hub manda a sessão a cada pergunta; persistir mantém /diagnose e
            # a próxima conversa coerentes mesmo sem /session/sync explícito.
            st.upsert_campaigns(body.session.campaigns)
            st.save_session(body.session.model_dump())
        rag_hits = []
        if body.use_rag:
            rag_hits = rg.query(body.message, collection="personas", limit=body.limit)
            if not rag_hits:
                rag_hits = rg.query(body.message, collection="research", limit=body.limit)
            if not rag_hits:
                # Contexto semântico das skills ingeridas no boot (2 hits curtos).
                rag_hits = rg.query(
                    body.message,
                    collection="skills",
                    limit=min(body.limit, 2),
                )

        selected = select_skills(body.message)
        skills_used = [str(s.get("id")) for s in selected]
        fails = fail_guard(session.campaigns)

        if body.use_llm and getattr(client, "available", False):
            messages = [
                {"role": "system", "content": build_system_prompt(selected)},
                {"role": "user", "content": build_user_prompt(body.message, session, rag_hits)},
            ]
            result = await client.complete(messages)
            if result:
                resp = ChatResponse(
                    reply=result.text,
                    mode="llm",
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
    async def diag(body: DiagnoseRequest, _: None = Depends(require_token)) -> DiagnoseResponse:
        session = body.session or SessionSyncRequest()
        if body.campaign_id:
            session.campaigns = [c for c in session.campaigns if c.id == body.campaign_id]
        return diagnose(session)

    @app.post("/decisions", response_model=DecisionOut)
    async def record_decision(body: DecisionIn, request: Request, _: None = Depends(require_token)) -> DecisionOut:
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

    return app

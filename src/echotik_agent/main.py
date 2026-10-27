"""FastAPI app do sidecar EchoTok Agent."""

from __future__ import annotations

import hmac
import json
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    ChatRequest,
    ChatResponse,
    DecisionIn,
    DecisionOut,
    DiagnoseRequest,
    DiagnoseResponse,
    RagIngestRequest,
    RagQueryRequest,
    SeedPersonasRequest,
    SessionSyncRequest,
)
from .rag import RAG
from .rules import answer, diagnose
from .store import Store


AUTH_HEADER = Annotated[str | None, Header(alias="Authorization")]


def create_app(settings: Settings | None = None, store: Store | None = None, rag: RAG | None = None) -> FastAPI:
    cfg = settings or __import__("echotik_agent.config", fromlist=["load_settings"]).load_settings()
    st = store or Store(cfg.sqlite_path)
    rg = rag or RAG(cfg.lancedb_path, dim=cfg.embedding_dim)

    app = FastAPI(title="hermes-echotik-agent", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = cfg
    app.state.store = st
    app.state.rag = rg

    def require_token(authorization: AUTH_HEADER = None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED"})
        provided = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(provided, cfg.token):
            raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED"})

    @app.exception_handler(Exception)
    async def internal(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "INTERNAL", "message": "erro interno"}, status_code=500)

    @app.get("/health")
    async def health() -> dict:
        if not cfg.allow_unauthenticated_health:
            require_token()
        return {"status": "ok", "service": "echotik-agent", "version": "0.1.0"}

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
    async def session_sync(body: SessionSyncRequest, _: None = Depends(require_token)) -> dict:
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
        return {"ok": True, "campaigns": len(body.campaigns)}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, _: None = Depends(require_token)) -> ChatResponse:
        session = body.session
        if session is None:
            # tenta última sessão salva não disponível; usa vazio
            session = SessionSyncRequest()
        rag_hits = []
        if body.use_rag:
            rag_hits = rg.query(body.message, collection="personas", limit=body.limit)
            if not rag_hits:
                rag_hits = rg.query(body.message, collection="research", limit=body.limit)
        resp = answer(body.message, session, rag_hits)
        return resp

    @app.post("/diagnose", response_model=DiagnoseResponse)
    async def diag(body: DiagnoseRequest, _: None = Depends(require_token)) -> DiagnoseResponse:
        session = body.session or SessionSyncRequest()
        if body.campaign_id:
            session.campaigns = [c for c in session.campaigns if c.id == body.campaign_id]
        return diagnose(session)

    @app.post("/decisions", response_model=DecisionOut)
    async def record_decision(body: DecisionIn, _: None = Depends(require_token)) -> DecisionOut:
        return st.record_decision(body)

    @app.get("/decisions")
    async def list_decisions(limit: int = 50, _: None = Depends(require_token)) -> list:
        return [d.model_dump(by_alias=True) for d in st.list_decisions(limit)]

    @app.post("/rag/ingest")
    async def rag_ingest(body: RagIngestRequest, _: None = Depends(require_token)) -> dict:
        n = rg.ingest(body.docs, collection=body.collection)
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

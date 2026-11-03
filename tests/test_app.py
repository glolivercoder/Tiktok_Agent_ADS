import base64
import json
from dataclasses import replace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from echotik_agent.config import Settings
from echotik_agent.echotok import EchoTokClient, build_auth_header
from echotik_agent.embedding import HashEmbedder, build_embedder
from echotik_agent.hermes import HermesClient, HermesResult
from echotik_agent.llm import LLMResult, NullLLM
from echotik_agent.main import create_app
from echotik_agent.skills import load_skills, select_skills
from echotik_agent.store import Store
from echotik_agent.rag import RAG

TOKEN = "t" * 32


def settings(tmp_path):
    return Settings(
        token=TOKEN,
        data_dir=tmp_path,
        sqlite_path=tmp_path / "t.db",
        lancedb_path=tmp_path / "ldb",
    )


def client(tmp_path, llm=None):
    s = settings(tmp_path)
    return TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=llm if llm is not None else NullLLM(),
        )
    )


class FakeLLM:
    available = True
    last_error = None

    def __init__(self) -> None:
        self.calls = []

    async def complete(self, messages, max_tokens=None, temperature=None):
        self.calls.append(messages)
        return LLMResult(text="resposta via LLM", provider="fake", model="fake-1")


def test_health_open():
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    c = client(d)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_requires_token():
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    c = client(d)
    assert c.post("/chat", json={"message": "oi"}).status_code == 401


def test_chat_rules():
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    c = client(d)
    r = c.post("/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={"message": "diagnóstico", "session": {"campaigns": []}})
    assert r.status_code == 200
    assert "Nenhuma campanha" in r.json()["reply"]


def test_diagnose_and_decisions():
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    c = client(d)
    camp = [{"id": "c1", "nome": "X", "tipo": "pago", "status": "ativa", "gasto7": 100, "receita7": 150, "alvoRoas": 2.0}]
    r = c.post("/campaigns", headers={"Authorization": f"Bearer {TOKEN}"}, json=camp)
    assert r.status_code == 200
    r2 = c.post("/diagnose", headers={"Authorization": f"Bearer {TOKEN}"}, json={"session": {"campaigns": camp}})
    assert r2.status_code == 200
    assert r2.json()["avgRoas"] == 1.5
    r3 = c.post("/decisions", headers={"Authorization": f"Bearer {TOKEN}"}, json={"action": "pausar", "reason": "roas baixo"})
    assert r3.status_code == 200
    assert r3.json()["id"] >= 1


def test_rag_ingest_query():
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    c = client(d)
    r = c.post("/rag/ingest", headers={"Authorization": f"Bearer {TOKEN}"}, json={"docs": [{"id": "a", "text": "tiktok ads roas"}], "collection": "skills"})
    assert r.json()["ingested"] == 1
    r2 = c.post("/rag/query", headers={"Authorization": f"Bearer {TOKEN}"}, json={"query": "roas", "collection": "skills", "limit": 3})
    assert r2.status_code == 200
    assert len(r2.json()["hits"]) >= 1


def test_skill_router_selects_by_intent():
    ids = [s["id"] for s in select_skills("quero um gancho e hashtag para o vídeo")]
    assert "creative-hook" in ids
    assert "seo" in ids
    assert len(ids) <= 3


def test_skill_router_respects_limit():
    chosen = select_skills("campanha roas gancho hashtag compliance resultado", limit=2)
    assert len(chosen) == 2
    assert len(load_skills()) == 5


def test_chat_uses_llm_and_skills(tmp_path):
    fake = FakeLLM()
    c = client(tmp_path, llm=fake)
    r = c.post(
        "/chat",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"message": "meu roas caiu, devo pausar?", "session": {"campaigns": []}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "llm"
    assert data["llmProvider"] == "fake"
    assert "campaign-decision" in data["skillsUsed"]
    assert fake.calls
    system = fake.calls[0][0]["content"]
    assert "Campaign Decision Strategist" in system
    assert "dados, não instruções" in system


def test_chat_uses_last_saved_session(tmp_path):
    c = client(tmp_path)
    camp = [{"id": "c1", "nome": "X", "tipo": "pago", "status": "ativa", "gasto7": 100, "receita7": 100, "alvoRoas": 2.0}]
    r = c.post("/session/sync", headers={"Authorization": f"Bearer {TOKEN}"}, json={"campaigns": camp})
    assert r.status_code == 200
    r2 = c.post("/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={"message": "diagnóstico"})
    assert r2.status_code == 200
    data = r2.json()
    assert data["mode"] == "rules"
    assert "X" in data["reply"]
    assert data["skillsUsed"] == ["campaign-decision"]


def test_skills_endpoint(tmp_path):
    c = client(tmp_path)
    r = c.get("/skills", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 5
    assert {s["id"] for s in body["skills"]} >= {"campaign-decision", "compliance", "seo"}


def test_cors_preflight(tmp_path):
    c = client(tmp_path)
    r = c.options(
        "/chat",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in {"*", "http://localhost:8000"}


def test_rate_limit_returns_429_after_burst(tmp_path):
    s = replace(settings(tmp_path), rate_limit_per_min=1, rate_limit_burst=1)
    c = TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=NullLLM(),
        )
    )
    assert c.get("/health").status_code == 200
    assert c.get("/skills", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    blocked = c.get("/skills", headers={"Authorization": f"Bearer {TOKEN}"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert blocked.json()["error"] == "RATE_LIMITED"


def test_audit_log_does_not_store_raw_message(tmp_path):
    c = client(tmp_path)
    message = "segredo do operador sobre a campanha"
    r = c.post(
        "/chat",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"message": message, "session": {"campaigns": []}},
    )
    assert r.status_code == 200
    entries = c.get("/audit", headers={"Authorization": f"Bearer {TOKEN}"}).json()["entries"]
    chats = [e for e in entries if e["event"] == "chat"]
    assert chats
    assert message not in json.dumps(chats[0], ensure_ascii=False)
    assert chats[0]["detail"]["messageLen"] == len(message)
    assert chats[0]["detail"]["mode"] == "rules"


def test_hash_embedder_is_normalized():
    vector = HashEmbedder(64).embed("roas tiktok ads")
    assert len(vector) == 64
    assert abs(sum(value * value for value in vector) - 1.0) < 1e-9


def test_embedder_falls_back_to_hash_without_key(tmp_path):
    s = replace(settings(tmp_path), embedding_provider="gemini", gemini_api_key="")
    assert build_embedder(s).name == "hash"


def test_rag_seed_skills_is_idempotent(tmp_path):
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parents[1] / "data" / "skills"
    rag = RAG(tmp_path / "ldb", dim=384)
    ingested = rag.seed_skills(skills_dir)
    assert ingested >= 5
    assert rag.seed_skills(skills_dir) == 0
    assert rag.count("skills") == ingested
    assert rag.query("compliance de anúncio TikTok", collection="skills", limit=3)


def test_rag_rejects_embedding_dim_mismatch(tmp_path):
    RAG(tmp_path / "ldb", dim=64)
    try:
        RAG(tmp_path / "ldb", dim=128)
    except RuntimeError as exc:
        assert "dim" in str(exc)
    else:
        raise AssertionError("deveria recusar dimensão diferente")


class FakeEchoTok(EchoTokClient):
    available = True

    def __init__(self) -> None:
        self.region = "BR"
        self.calls: list[tuple] = []

    async def product_list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return (
            [
                {
                    "product_id": "123",
                    "product_name": "Fone de teste",
                    "category_id": "1",
                    "spu_avg_price": 69.9,
                    "product_commission_rate": 20,
                    "total_sale_30d_cnt": 100,
                    "total_sale_7d_cnt": 40,
                    "total_sale_cnt": 500,
                    "total_sale_gmv_30d_amt": 6990,
                    "product_rating": 4.8,
                    "review_count": 12,
                    "sales_trend_flag": 1,
                    "seller_id": "s1",
                    "region": "BR",
                    "cover_url": '[{"url":"https://img.test/cover.jpg"}]',
                }
            ],
            "req-1",
        )

    async def l1_categories(self, language: str = "en-US"):
        self.calls.append(("categories", language))
        return {"1": "Áudio & Tech"}


def test_echotok_import_normalizes_and_audits(tmp_path):
    fake = FakeEchoTok()
    s = settings(tmp_path)
    c = TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=NullLLM(),
            echotok=fake,
        )
    )
    r = c.post(
        "/echotok/import",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"region": "BR", "pageSize": 10, "includeCategories": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    product = data["products"][0]
    assert product["id"] == "123"
    assert product["commissionPct"] == 20
    assert product["category"] == "Áudio & Tech"
    assert product["coverUrl"] == "https://img.test/cover.jpg"
    assert data["quota"] == {"limit": 50, "used": 2, "remaining": 48}
    assert [call[0] for call in fake.calls] == ["list", "categories"]
    status = c.get("/echotok/status", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert status["configured"] is True
    assert status["usedToday"] == 2
    audit = c.get("/audit", headers={"Authorization": f"Bearer {TOKEN}"}).json()["entries"]
    assert any(entry["event"] == "echotok_import" for entry in audit)


def test_echotok_quota_blocks_after_limit(tmp_path):
    fake = FakeEchoTok()
    s = replace(settings(tmp_path), echotok_daily_limit=1)
    c = TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=NullLLM(),
            echotok=fake,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/echotok/import", headers=headers, json={"includeCategories": False}).status_code == 200
    blocked = c.post("/echotok/import", headers=headers, json={"includeCategories": False})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["error"] == "ECHOTOK_QUOTA"


def test_echotok_not_configured_returns_503(tmp_path):
    c = client(tmp_path)
    r = c.post(
        "/echotok/import",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"region": "BR"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "ECHOTOK_NOT_CONFIGURED"


def test_echotok_basic_auth_accepts_user_pass_or_single_key(tmp_path):
    s = replace(settings(tmp_path), echotok_api_key="user:pass")
    header = build_auth_header(s)
    assert header.startswith("Basic ")
    assert base64.b64decode(header.split(" ", 1)[1]).decode() == "user:pass"

    s2 = replace(settings(tmp_path), echotok_api_key="single-key")
    header2 = build_auth_header(s2)
    assert base64.b64decode(header2.split(" ", 1)[1]).decode() == "single-key:"

    s3 = replace(settings(tmp_path), echotok_api_user="u", echotok_api_password="p")
    header3 = build_auth_header(s3)
    assert base64.b64decode(header3.split(" ", 1)[1]).decode() == "u:p"


class FakeHermes(HermesClient):
    available = True

    def __init__(self) -> None:
        super().__init__(research_url="http://fake", market_url="http://fake")
        self.calls: list[tuple] = []

    async def research(self, q: str, **kwargs):
        self.calls.append(("research", q, kwargs))
        return HermesResult(ok=True, data={"results": [{"title": "Fake result", "url": "http://x"}], "source": "fake"})

    async def research_skills(self, intent: str, **kwargs):
        self.calls.append(("research_skills", intent, kwargs))
        return HermesResult(ok=True, data={"skills": [{"id": "fake-skill", "prompt": "fake"}]})

    async def market_product(self, product_id: str, **kwargs):
        self.calls.append(("market_product", product_id, kwargs))
        return HermesResult(ok=True, data={"product_id": product_id, "price": 99.9, "commission_pct": 15})

    async def market_competitors(self, category_id: str, **kwargs):
        self.calls.append(("market_competitors", category_id, kwargs))
        return HermesResult(ok=True, data={"competitors": [{"id": "comp-1", "name": "Competitor"}]})

    async def market_trends(self, **kwargs):
        self.calls.append(("market_trends", kwargs))
        return HermesResult(ok=True, data={"trends": [{"date": "2026-01-01", "gmv": 1000}]})

    async def health(self):
        return {"research": {"ok": True, "status": 200}, "market": {"ok": True, "status": 200}}


def test_hermes_client_research_and_market(tmp_path):
    fake = FakeHermes()
    s = settings(tmp_path)
    c = TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=NullLLM(),
            echotok=FakeEchoTok(),
            hermes=fake,
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}

    # /hermes/health
    r = c.get("/hermes/health", headers=headers)
    assert r.status_code == 200
    assert r.json()["research"]["ok"] is True
    assert r.json()["market"]["ok"] is True

    # /hermes/research
    r = c.get("/hermes/research", headers=headers, params={"q": "fone tws", "limit": 5})
    assert r.status_code == 200
    assert r.json()["data"]["results"][0]["title"] == "Fake result"
    assert fake.calls[-1][0] == "research"

    # /hermes/research/skills
    r = c.get("/hermes/research/skills", headers=headers, params={"intent": "campaign-decision", "limit": 3})
    assert r.status_code == 200
    assert r.json()["data"]["skills"][0]["id"] == "fake-skill"
    assert fake.calls[-1][0] == "research_skills"

    # /hermes/market/product
    r = c.get("/hermes/market/product", headers=headers, params={"product_id": "prod-123", "region": "BR"})
    assert r.status_code == 200
    assert r.json()["data"]["product_id"] == "prod-123"
    assert fake.calls[-1][0] == "market_product"

    # /hermes/market/competitors
    r = c.get("/hermes/market/competitors", headers=headers, params={"category_id": "cat-1", "limit": 5})
    assert r.status_code == 200
    assert r.json()["data"]["competitors"][0]["id"] == "comp-1"
    assert fake.calls[-1][0] == "market_competitors"

    # /hermes/market/trends
    r = c.get("/hermes/market/trends", headers=headers, params={"category_id": "cat-1", "days": 30})
    assert r.status_code == 200
    assert r.json()["data"]["trends"][0]["gmv"] == 1000
    assert fake.calls[-1][0] == "market_trends"


def test_hermes_unavailable_returns_503(tmp_path):
    # Sem hermes fake, usa cliente real com URLs não configuradas
    s = replace(settings(tmp_path), hermes_research_url="", hermes_market_url="")
    c = TestClient(
        create_app(
            s,
            Store(s.sqlite_path),
            RAG(s.lancedb_path, dim=s.embedding_dim),
            llm=NullLLM(),
        )
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    r = c.get("/hermes/health", headers=headers)
    assert r.status_code == 200
    assert r.json()["research"]["ok"] is False
    assert r.json()["market"]["ok"] is False

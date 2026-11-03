from fastapi.testclient import TestClient

from echotik_agent.config import Settings
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

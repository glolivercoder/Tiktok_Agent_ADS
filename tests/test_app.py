from fastapi.testclient import TestClient

from echotik_agent.config import Settings
from echotik_agent.main import create_app
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


def client(tmp_path):
    s = settings(tmp_path)
    return TestClient(create_app(s, Store(s.sqlite_path), RAG(s.lancedb_path, dim=s.embedding_dim)))


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

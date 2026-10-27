"""SQLite store: campanhas, decisões, sessões. Sem segredos aqui."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CampaignIn, CampaignOut, DecisionIn, DecisionOut


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc(c: CampaignIn | dict[str, Any]) -> CampaignOut:
    data = c if isinstance(c, dict) else c.model_dump()
    gasto = float(data.get("gasto7") or 0)
    receita = float(data.get("receita7") or 0)
    roas = round(receita / gasto, 2) if gasto > 0 else None
    acos = round(gasto / receita, 4) if receita > 0 else None
    return CampaignOut(**{**data, "roas": roas, "acos": acos})


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    outcome_roas_3d REAL,
                    outcome_roas_7d REAL,
                    approved INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert_campaigns(self, campaigns: list[CampaignIn]) -> list[CampaignOut]:
        out: list[CampaignOut] = []
        with self._conn() as c:
            for camp in campaigns:
                co = _calc(camp)
                c.execute(
                    "INSERT INTO campaigns(id,data,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                    (camp.id, json.dumps(co.model_dump(), ensure_ascii=False), _now()),
                )
                out.append(co)
        return out

    def list_campaigns(self) -> list[CampaignOut]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM campaigns ORDER BY id").fetchall()
        return [CampaignOut(**json.loads(r["data"])) for r in rows]

    def save_session(self, payload: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(payload,created_at) VALUES(?,?)",
                (json.dumps(payload, ensure_ascii=False), _now()),
            )

    def record_decision(self, d: DecisionIn) -> DecisionOut:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO decisions(campaign_id,action,reason,metrics,outcome_roas_3d,outcome_roas_7d,approved,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    d.campaign_id,
                    d.action.value,
                    d.reason,
                    json.dumps(d.metrics, ensure_ascii=False),
                    d.outcome_roas_3d,
                    d.outcome_roas_7d,
                    None if d.approved is None else int(d.approved),
                    _now(),
                ),
            )
            did = cur.lastrowid
            row = c.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
        return DecisionOut(
            id=row["id"],
            campaign_id=row["campaign_id"],
            action=row["action"],
            reason=row["reason"],
            metrics=json.loads(row["metrics"]),
            outcome_roas_3d=row["outcome_roas_3d"],
            outcome_roas_7d=row["outcome_roas_7d"],
            approved=None if row["approved"] is None else bool(row["approved"]),
            created_at=row["created_at"],
        )

    def list_decisions(self, limit: int = 50) -> list[DecisionOut]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                DecisionOut(
                    id=r["id"],
                    campaign_id=r["campaign_id"],
                    action=r["action"],
                    reason=r["reason"],
                    metrics=json.loads(r["metrics"]),
                    outcome_roas_3d=r["outcome_roas_3d"],
                    outcome_roas_7d=r["outcome_roas_7d"],
                    approved=None if r["approved"] is None else bool(r["approved"]),
                    created_at=r["created_at"],
                )
            )
        return out

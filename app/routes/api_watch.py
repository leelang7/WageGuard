"""S4 워치리스트 — 사업장 등록 후 주기적으로 NTS 재조회 + 상태 변화 알림"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn
from .api_business import call_nts, search_defaulters, lookup_cell, compute_risk
from .api_cluster import add_signal

router = APIRouter(prefix="/api/watch")


class WatchAdd(BaseModel):
    label: str
    bno: str | None = None
    company_query: str | None = None
    notes: str | None = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _refresh(watch_id: int) -> dict:
    """등록된 워치 1건의 상태를 다시 계산하고 변경되면 이벤트 생성."""
    with conn() as c:
        w = c.execute("SELECT * FROM watchlist WHERE id=?", (watch_id,)).fetchone()
    if not w:
        raise HTTPException(404, "watch not found")
    w = dict(w)

    nts = None
    nts_status = ""
    hits: list[dict] = []
    industry = None
    region = None

    if w["bno"]:
        nts, _, _ = call_nts(w["bno"])
        nts_status = nts.get("b_stt", "") if nts else ""

    if w["company_query"]:
        hits = search_defaulters(w["company_query"])
        if hits:
            industry = hits[0]["industry"]
            region = hits[0]["region"]

    cell = lookup_cell(industry, region)
    risk = compute_risk(nts, hits, cell)
    new_score = risk["score"]
    new_status = nts_status or ("MATCHED" if hits else "OK")

    with conn() as c:
        events = []
        if w["last_status"] and w["last_status"] != new_status:
            events.append(
                ("status_change", json.dumps({"from": w["last_status"], "to": new_status}, ensure_ascii=False))
            )
        if w["last_score"] is not None and abs((new_score or 0) - (w["last_score"] or 0)) >= 10:
            events.append(
                ("score_jump", json.dumps({"from": w["last_score"], "to": new_score}, ensure_ascii=False))
            )
        if not w["last_checked_at"]:
            events.append(
                ("registered", json.dumps({"score": new_score, "status": new_status}, ensure_ascii=False))
            )

        for ev_type, detail in events:
            c.execute(
                "INSERT INTO watchlist_events (watch_id, event_type, detail, created_at) VALUES (?,?,?,?)",
                (watch_id, ev_type, detail, now_iso()),
            )

        c.execute(
            """UPDATE watchlist SET last_status=?, last_score=?, last_checked_at=? WHERE id=?""",
            (new_status, new_score, now_iso(), watch_id),
        )

    return {"id": watch_id, "status": new_status, "score": new_score, "events": [e[0] for e in events]}


@router.get("")
def list_all() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT id, label, bno, company_query, last_status, last_score,
                      last_checked_at, created_at, notes
               FROM watchlist ORDER BY (last_score IS NULL), last_score DESC, id"""
        ).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            evs = c.execute(
                "SELECT event_type, detail, created_at FROM watchlist_events WHERE watch_id=? ORDER BY id DESC LIMIT 5",
                (r["id"],),
            ).fetchall()
            r["events"] = [dict(e) for e in evs]
            out.append(r)
    return out


@router.post("")
def add(item: WatchAdd) -> dict:
    if not item.bno and not item.company_query:
        raise HTTPException(400, "bno 또는 company_query 중 하나 필수")
    with conn() as c:
        cur = c.execute(
            """INSERT INTO watchlist
               (label, bno, company_query, last_status, last_score, last_checked_at, created_at, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (item.label, item.bno, item.company_query, None, None, None, now_iso(), item.notes),
        )
        new_id = cur.lastrowid
    res = _refresh(new_id)
    if item.company_query:
        add_signal(
            company_raw=item.company_query,
            channel="watch",
            domain="meta",   # 워치 등록은 사용자 관심 표시일 뿐 신호 도메인 아님
            severity="low",
            source_ref=str(new_id),
        )
    return res


@router.post("/{watch_id}/refresh")
def refresh(watch_id: int) -> dict:
    return _refresh(watch_id)


@router.post("/refresh-all")
def refresh_all() -> dict:
    with conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM watchlist").fetchall()]
    results = []
    for i in ids:
        try:
            results.append(_refresh(i))
        except Exception as e:
            results.append({"id": i, "error": str(e)})
    return {"refreshed": len(results), "results": results}


@router.delete("/{watch_id}")
def remove(watch_id: int) -> dict:
    with conn() as c:
        c.execute("DELETE FROM watchlist_events WHERE watch_id=?", (watch_id,))
        c.execute("DELETE FROM watchlist WHERE id=?", (watch_id,))
    return {"removed": watch_id}

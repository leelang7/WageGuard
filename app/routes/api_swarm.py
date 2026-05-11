"""TRIZ #22 (전화위복) — 집단 보호 알림.

신고가 발생하면 같은 업종·지역 워치리스트 등록자에 익명 통계 알림.
'당신 사업장 인근 OO업종에서 체불 신고 N건 누적' →
본인 사업장도 점검·증거 수집 시작 유도. 신고가 신고를 부르는 선순환.

TRIZ #24 (매개체) — 임금 약속 공시.
사업주가 임금 지급일을 시스템에 약속 → 그 날 ±N일 NPS·NTS 변동 자동 모니터링.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import conn
from .api_cluster import normalize as normalize_company
from .api_notify import push_notification

router = APIRouter(prefix="/api/swarm")


@router.get("/peer-alerts")
def peer_alerts(industry: str | None = None, region: str | None = None) -> dict:
    """같은 업종·지역 군집 알림 — 7일 내 신고 누적 통계 (개별 신고 비공개)."""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    args: list = [cutoff]
    sql = """SELECT industry, region, COUNT(*) AS n, COUNT(DISTINCT company) AS n_companies
             FROM cases WHERE created_at >= ?"""
    if industry:
        sql += " AND industry = ?"
        args.append(industry)
    if region:
        sql += " AND region = ?"
        args.append(region)
    sql += " GROUP BY industry, region HAVING n >= 2 ORDER BY n DESC"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        out.append({
            "industry": r["industry"],
            "region": r["region"],
            "n_reports_7d": r["n"],
            "n_companies": r["n_companies"],
            "message": f"{r['region'] or '지역미상'} {r['industry'] or '업종미상'}에서 7일간 체불 신고 {r['n']}건 누적 (사업장 {r['n_companies']}곳). 본인 사업장도 점검·증거 보관 권장.",
        })
    return {"alerts": out, "as_of": datetime.now().isoformat(timespec="seconds")}


@router.post("/broadcast-peer")
def broadcast_peer() -> dict:
    """피어 알림을 워치리스트 등록자에게 brodcast (시스템 → 인박스)."""
    data = peer_alerts()
    n_pushed = 0
    for a in data["alerts"]:
        push_notification(
            audience="worker",
            severity="warning",
            title=f"⚠ 인근 체불 신고 누적 — {a['region']} / {a['industry']}",
            body=a["message"],
            link="/cases",
        )
        n_pushed += 1
    return {"broadcast": n_pushed}


# ────────────────────────────────────────────────
# 임금 약속 공시 (TRIZ #24 매개체)
# ────────────────────────────────────────────────

class PromiseIn(BaseModel):
    company: str
    promised_date: str   # YYYY-MM-DD
    note: str | None = None


@router.post("/promise")
def add_promise(inp: PromiseIn) -> dict:
    norm = normalize_company(inp.company)
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            """INSERT INTO pay_promises (company, company_norm, promised_date, note, created_at)
               VALUES (?,?,?,?,?)""",
            (inp.company, norm, inp.promised_date, inp.note, now),
        )
        rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    push_notification(
        audience="worker",
        severity="info",
        title=f"✋ 임금 지급 약속 공시 — {inp.company}",
        body=f"사업주가 {inp.promised_date} 지급을 시스템에 공시. 위반 시 자동 신호.",
        link=f"/company/{inp.company}",
    )
    return {"id": rid, "promised_date": inp.promised_date}


@router.get("/promises/{company}")
def get_promises(company: str) -> list[dict]:
    norm = normalize_company(company)
    with conn() as c:
        rows = c.execute(
            """SELECT id, promised_date, note, fulfilled, fulfilled_at,
                      violation_logged, created_at
               FROM pay_promises WHERE company_norm = ?
               ORDER BY promised_date DESC""",
            (norm,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/promise/{pid}/fulfilled")
def mark_fulfilled(pid: int) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute("UPDATE pay_promises SET fulfilled = 1, fulfilled_at = ? WHERE id = ?",
                  (now, pid))
    return {"ok": True}


@router.get("/promises/_overdue")
def overdue_promises() -> list[dict]:
    """약속일 + 7일 경과해도 미이행인 사업장."""
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    with conn() as c:
        rows = c.execute(
            """SELECT * FROM pay_promises
               WHERE fulfilled = 0 AND promised_date < ?
               ORDER BY promised_date""",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]

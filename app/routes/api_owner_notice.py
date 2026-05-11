"""TRIZ #10 사전작용 — 사업주 자동 안내 + 소명 채널.

위험 신호 누적된 사업장의 사업주가 시스템에 구독 등록하면, 신호 발생 시
자동 알림. 사업주는 소명 응답 → 감독관 검토 → 일부 신호 가중치 조정.
"체불 발생 후 처리"가 아니라 "위험 신호 단계에서 자정"을 유도.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn
from .api_cluster import normalize as normalize_company
from .api_notify import push_notification

router = APIRouter(prefix="/api/owner-notice")


class SubscribeIn(BaseModel):
    company: str
    bzowr_rgst_no: str | None = None
    owner_name: str
    owner_contact: str


@router.post("/subscribe")
def subscribe(inp: SubscribeIn) -> dict:
    norm = normalize_company(inp.company)
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            """INSERT INTO owner_subscriptions
               (company, company_norm, bzowr_rgst_no, owner_name, owner_contact, created_at)
               VALUES (?,?,?,?,?,?)""",
            (inp.company, norm, inp.bzowr_rgst_no, inp.owner_name, inp.owner_contact, now),
        )
        rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": rid, "company_norm": norm}


@router.get("/check/{company}")
def check_signals(company: str) -> dict:
    """사업주가 자기 사업장 위험 신호 조회 (소명 기회 안내)."""
    norm = normalize_company(company)
    with conn() as c:
        sigs = c.execute(
            """SELECT channel, domain, severity, source_ref, created_at
               FROM company_signals WHERE company_norm = ?
               ORDER BY id DESC LIMIT 30""",
            (norm,),
        ).fetchall()
        cases = c.execute(
            "SELECT case_no, status, risk_score, created_at FROM cases WHERE company = ? ORDER BY id DESC LIMIT 10",
            (company,),
        ).fetchall()
        responses = c.execute(
            "SELECT response_text, accepted, created_at FROM owner_responses WHERE company_norm = ? ORDER BY id DESC LIMIT 5",
            (norm,),
        ).fetchall()
    sigs = [dict(s) for s in sigs]
    distinct_domains = sorted({s["domain"] for s in sigs if s["domain"] and s["domain"] != "meta"})

    risk_message = None
    if len(distinct_domains) >= 2 or len([s for s in sigs if s["severity"] == "high"]) >= 2:
        risk_message = (
            "🚨 신호 다중 도메인 누적 — 시스템이 임금체불 의심으로 분류했습니다. "
            "아래 소명 채널로 사실관계를 등록하시면 감독관이 검토합니다."
        )
    elif sigs:
        risk_message = "⚠ 일부 위험 신호가 감지됐습니다. 사전에 사업장 자가진단 + 정직 인증을 권장합니다."

    return {
        "company": company,
        "n_signals": len(sigs),
        "distinct_domains": distinct_domains,
        "signals": sigs,
        "cases": [dict(c) for c in cases],
        "previous_responses": [dict(r) for r in responses],
        "risk_message": risk_message,
        "actions": {
            "self_check": "/owner",
            "attest": "/attest",
            "respond": f"/api/owner-notice/respond/{normalize_company(company)}",
        },
    }


class RespondIn(BaseModel):
    company: str
    response_text: str


@router.post("/respond")
def respond(inp: RespondIn) -> dict:
    if len(inp.response_text.strip()) < 30:
        raise HTTPException(400, "소명 내용이 너무 짧습니다 (30자 이상).")
    norm = normalize_company(inp.company)
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            """INSERT INTO owner_responses (company, company_norm, response_text, created_at)
               VALUES (?,?,?,?)""",
            (inp.company, norm, inp.response_text, now),
        )
        rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    push_notification(
        audience="supervisor",
        severity="info",
        title=f"📨 사업주 소명 등록 — {inp.company}",
        body=f"소명 길이 {len(inp.response_text)}자. 검토 필요.",
        link=f"/owner-notice/{inp.company}",
    )
    return {"id": rid, "company_norm": norm, "submitted_at": now}


@router.post("/notify/{company}")
def notify_owner(company: str) -> dict:
    """감독관·시스템이 사업주 구독자에 신호 알림 보내기."""
    norm = normalize_company(company)
    with conn() as c:
        rows = c.execute(
            "SELECT id, owner_name, owner_contact FROM owner_subscriptions WHERE company_norm = ?",
            (norm,),
        ).fetchall()
    n = 0
    for r in rows:
        push_notification(
            audience="owner",
            severity="warning",
            title=f"⚠ 사업장 위험 신호 — {company}",
            body=f"{r['owner_name'] or ''} 님, 사업장에 위험 신호가 누적됐습니다. 소명·자가진단을 권장합니다.",
            link=f"/owner-notice/{company}",
        )
        with conn() as c:
            c.execute(
                "UPDATE owner_subscriptions SET last_alerted_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), r["id"]),
            )
        n += 1
    return {"notified": n}

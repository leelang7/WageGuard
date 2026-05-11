"""TRIZ #25 + #20 — 근로자 매월 임금 체크인.

핵심: 시스템이 사업장 데이터를 쫓아가지 않는다. 근로자가 매월 1탭으로 자기 사업장의
임금 수령 사실을 등록한다. 한 명만 등록해도 그 회사가 시스템에 들어온다.

사업주 자가인증(TRIZ-A) "지급함" + 근로자 체크인 "안 받음" → **일치도 0% = 강력한 거짓신고
탐지**. 이게 데이터 없는 회사를 잡는 유일한 진짜 사전 탐지.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import conn
from .api_cluster import add_signal, normalize as normalize_company

router = APIRouter(prefix="/api/checkin")

VALID_STATUS = {"received", "late", "partial", "unpaid"}
SEVERITY_FROM_STATUS = {
    "received": "low",
    "late":     "medium",
    "partial":  "medium",
    "unpaid":   "high",
}


class CheckinIn(BaseModel):
    company: str
    worker_alias: str | None = None
    contact: str                    # 익명화 위해 해시만 저장
    period_ym: str                  # YYYY-MM
    status: str
    paid_amount: int = 0
    paid_date: str | None = None
    expected_date: str | None = None
    note: str | None = None


def _hash_contact(c: str) -> str:
    return hashlib.sha256((c or "").encode("utf-8")).hexdigest()[:16]


@router.post("/submit")
def submit(inp: CheckinIn, request: Request) -> dict:
    if inp.status not in VALID_STATUS:
        raise HTTPException(400, f"status는 {sorted(VALID_STATUS)}")
    norm = normalize_company(inp.company)
    contact_hash = _hash_contact(inp.contact)
    ip = request.client.host if request.client else ""
    now = datetime.now().isoformat(timespec="seconds")

    # 동일 (사업장+contact_hash+period) 중복 차단
    with conn() as c:
        dup = c.execute(
            "SELECT id FROM worker_checkins WHERE company_norm=? AND contact_hash=? AND period_ym=?",
            (norm, contact_hash, inp.period_ym),
        ).fetchone()
    if dup:
        raise HTTPException(409, "동일 기간·동일 사용자 중복 체크인입니다")

    with conn() as c:
        c.execute(
            """INSERT INTO worker_checkins
               (company, company_norm, worker_alias, contact_hash, period_ym,
                status, paid_amount, paid_date, expected_date, note, submitter_ip, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                inp.company, norm,
                inp.worker_alias or f"근로자_{contact_hash[:6]}",
                contact_hash, inp.period_ym, inp.status,
                inp.paid_amount, inp.paid_date, inp.expected_date,
                inp.note, ip, now,
            ),
        )

    # 신호 발사 (status=unpaid/late/partial 시)
    sig_info = None
    if inp.status in ("unpaid", "late", "partial"):
        sig_info = add_signal(
            company_raw=inp.company,
            channel="case", domain="pay_default",
            severity=SEVERITY_FROM_STATUS[inp.status],
            source_ref=f"checkin/{inp.period_ym}/{inp.status}",
            event_at=inp.expected_date or now,
        )

    # 일치도 검증
    cohort = aggregate_company(inp.company, inp.period_ym)
    return {
        "ok": True,
        "period": inp.period_ym,
        "company": inp.company,
        "cohort": cohort,
        "cluster": sig_info,
    }


def aggregate_company(company: str, period_ym: str | None = None) -> dict:
    """같은 사업장 같은 기간의 체크인 cohort 집계 + 사업주 자가인증과 일치도."""
    norm = normalize_company(company)
    sql = "SELECT status, COUNT(*) AS n FROM worker_checkins WHERE company_norm = ?"
    args: list = [norm]
    if period_ym:
        sql += " AND period_ym = ?"
        args.append(period_ym)
    sql += " GROUP BY status"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    n_total = sum(counts.values())
    n_unpaid = (counts.get("unpaid", 0) + counts.get("late", 0) + counts.get("partial", 0))

    # 사업주 자가인증과 비교
    with conn() as c:
        if period_ym:
            attest = c.execute(
                "SELECT period_ym, paid_total, employee_count FROM owner_attestations "
                "WHERE company_norm=? AND period_ym=? ORDER BY id DESC LIMIT 1",
                (norm, period_ym),
            ).fetchone()
        else:
            attest = c.execute(
                "SELECT period_ym, paid_total, employee_count FROM owner_attestations "
                "WHERE company_norm=? ORDER BY id DESC LIMIT 1",
                (norm,),
            ).fetchone()

    consistency = None
    consistency_label = None
    if attest and n_total >= 1:
        # 사업주: "지급함" (paid_total > 0) vs 근로자: 미수령 비율
        owner_paid = (attest["paid_total"] or 0) > 0
        worker_unpaid_ratio = n_unpaid / max(n_total, 1)
        if owner_paid and worker_unpaid_ratio >= 0.5:
            consistency = "MISMATCH"
            consistency_label = (
                f"⚠ 사업주는 지급 보고 / 근로자 {n_unpaid}/{n_total}명 미수령·지연·일부 — 거짓 자가인증 의심"
            )
        elif owner_paid and worker_unpaid_ratio == 0:
            consistency = "MATCH"
            consistency_label = "✓ 사업주·근로자 보고 일치 (정직 인증 강화)"
        else:
            consistency = "PARTIAL"
            consistency_label = f"부분 일치 (근로자 미수령 {worker_unpaid_ratio*100:.0f}%)"

    return {
        "company": company,
        "period_ym": period_ym,
        "total": n_total,
        "by_status": counts,
        "unpaid_ratio": round(n_unpaid / max(n_total, 1), 3),
        "owner_attestation": dict(attest) if attest else None,
        "consistency": consistency,
        "consistency_label": consistency_label,
    }


@router.get("/cohort/{company}")
def cohort(company: str, period_ym: str | None = None) -> dict:
    return aggregate_company(company, period_ym)


@router.get("/recent")
def recent(limit: int = 30) -> list[dict]:
    """최근 체크인 (개인정보 제외 — alias만)."""
    with conn() as c:
        rows = c.execute(
            """SELECT company, worker_alias, period_ym, status, paid_amount,
                      paid_date, expected_date, created_at
               FROM worker_checkins ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/_top-unpaid")
def top_unpaid() -> list[dict]:
    """미수령 보고가 많은 사업장 — 사용자 측 진짜 사전 탐지 결과."""
    with conn() as c:
        rows = c.execute(
            """SELECT company,
                      SUM(CASE WHEN status='unpaid' THEN 1 ELSE 0 END) AS unpaid,
                      SUM(CASE WHEN status='late' THEN 1 ELSE 0 END) AS late,
                      SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) AS partial,
                      COUNT(*) AS total,
                      COUNT(DISTINCT contact_hash) AS distinct_workers,
                      MAX(period_ym) AS last_period
               FROM worker_checkins
               GROUP BY company
               HAVING unpaid + late + partial >= 1
               ORDER BY (unpaid + late + partial) DESC, distinct_workers DESC
               LIMIT 30"""
        ).fetchall()
    return [dict(r) for r in rows]

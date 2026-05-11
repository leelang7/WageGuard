"""TRIZ #13(반대) + #25(셀프서비스) — 정직 사업주 자가 인증.

기존: 정부가 사업주를 잡는다 → 부담 + 사각지대
TRIZ 반대: 사업주가 매월 자기 정직성을 자가 입증 → 누적 통과 시 인증 마크 발급.
미인증 사업장은 자동으로 정직성 미입증 신호.

데이터:
  - 사업주가 매월 (회사명·기간·지급일·지급총액·가입자수·명세서 교부 여부) 자가 등록
  - 시스템은 일관성 검증 (전월 대비 급격 하락 / NPS 데이터와 비교)
  - 6개월 누적 통과 시 "WageGuard 정직 인증" 디지털 마크
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn
from .api_cluster import normalize as normalize_company

router = APIRouter(prefix="/api/attest")


class AttestIn(BaseModel):
    company: str
    bzowr_rgst_no: str | None = None
    representative: str | None = None
    contact: str
    period_ym: str            # YYYY-MM
    employee_count: int = 0
    payment_date: str         # YYYY-MM-DD
    paid_total: int = 0
    insured_count: int = 0
    payslip_issued: bool = True
    avg_hours: float = 0
    consent: bool = False


def _eval_consistency(items: list[dict]) -> dict:
    """월별 자가신고 일관성 검증."""
    if not items:
        return {"checks": [], "ok_months": 0, "issues": []}
    items_sorted = sorted(items, key=lambda r: r["period_ym"])
    issues = []
    ok = 0
    prev = None
    for it in items_sorted:
        ok_this = True
        # 명세서 미교부
        if not it["payslip_issued"]:
            issues.append({"period": it["period_ym"], "issue": "명세서 미교부 보고"})
            ok_this = False
        # 가입자수 < 직원수 (사회보험 누락 의심)
        if it["employee_count"] and it["insured_count"] and it["insured_count"] < it["employee_count"] * 0.7:
            issues.append({"period": it["period_ym"],
                           "issue": f"보험 가입자({it['insured_count']}) < 직원수({it['employee_count']})의 70% — 누락 의심"})
            ok_this = False
        # 전월 대비 지급총액 50% 이상 감소
        if prev and prev["paid_total"] and it["paid_total"]:
            drop = (prev["paid_total"] - it["paid_total"]) / prev["paid_total"]
            if drop > 0.5:
                issues.append({"period": it["period_ym"],
                               "issue": f"전월 대비 지급총액 {round(drop*100)}% 감소"})
                ok_this = False
        # 직원당 평균 지급액이 최저임금 환산보다 낮음
        if it["employee_count"] and it["paid_total"]:
            per_emp = it["paid_total"] / it["employee_count"]
            if per_emp < 1500000:
                issues.append({"period": it["period_ym"],
                               "issue": f"직원당 평균 {int(per_emp):,}원 — 최저임금 환산 미달 의심"})
                ok_this = False
        if ok_this:
            ok += 1
        prev = it
    return {"checks": items_sorted, "ok_months": ok, "issues": issues}


@router.post("/submit")
def submit(inp: AttestIn) -> dict:
    if not inp.consent:
        raise HTTPException(400, "자가신고 동의가 필요합니다.")
    norm = normalize_company(inp.company)
    payload = inp.dict()
    sha = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            """INSERT INTO owner_attestations
               (company, company_norm, bzowr_rgst_no, representative, contact,
                period_ym, employee_count, payment_date, paid_total, insured_count,
                payslip_issued, avg_hours, sha256, consent, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                inp.company, norm, inp.bzowr_rgst_no, inp.representative, inp.contact,
                inp.period_ym, inp.employee_count, inp.payment_date, inp.paid_total,
                inp.insured_count, int(inp.payslip_issued), inp.avg_hours,
                sha, int(inp.consent), now,
            ),
        )

    cert = certificate(inp.company)
    return {"ok": True, "sha256": sha, "certificate": cert}


@router.get("/certificate/{name}")
def certificate(name: str) -> dict:
    norm = normalize_company(name)
    with conn() as c:
        rows = c.execute(
            """SELECT period_ym, employee_count, payment_date, paid_total,
                      insured_count, payslip_issued, avg_hours, created_at
               FROM owner_attestations WHERE company_norm = ?
               ORDER BY period_ym DESC LIMIT 24""",
            (norm,),
        ).fetchall()
    items = [dict(r) for r in rows]
    if not items:
        return {
            "company": name,
            "status": "none",
            "label": "미인증 — 자가신고 없음",
            "months_attested": 0,
        }
    eval_ = _eval_consistency(items)

    # 인증 등급
    months = len({r["period_ym"] for r in items})
    ok_months = eval_["ok_months"]
    if ok_months >= 6:
        status = "verified_gold"
        label = "🏅 정직 사업주 (Gold) — 6개월+ 통과"
    elif ok_months >= 3:
        status = "verified_silver"
        label = "🥈 정직 사업주 (Silver) — 3개월+ 통과"
    elif ok_months >= 1:
        status = "in_progress"
        label = "🟡 인증 진행 중"
    else:
        status = "flagged"
        label = "🚨 자가신고 일관성 미달"

    return {
        "company": name,
        "status": status,
        "label": label,
        "months_attested": months,
        "ok_months": ok_months,
        "issues": eval_["issues"],
        "history": items[:6],
    }


@router.get("/list")
def list_certified(limit: int = 100) -> list[dict]:
    with conn() as c:
        groups = c.execute(
            """SELECT company, COUNT(DISTINCT period_ym) AS months,
                      MAX(created_at) AS last_at
               FROM owner_attestations GROUP BY company
               ORDER BY months DESC, last_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for g in groups:
        cert = certificate(g["company"])
        out.append({
            "company": g["company"],
            "months_attested": cert["months_attested"],
            "ok_months": cert["ok_months"],
            "status": cert["status"],
            "label": cert["label"],
            "last_at": g["last_at"],
        })
    return out

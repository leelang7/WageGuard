"""인텔 모듈 — Spike 탐지 + Salary Benchmark + Whistleblower 보호 가이드"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api/intel")


@router.get("/spike")
def spike() -> dict:
    """7일 내 같은 사업장에 신고가 급증하면 spike alert (집단 체불 가능성)."""
    cutoff_recent = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    cutoff_baseline = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
    with conn() as c:
        rows = c.execute(
            """SELECT company,
                      SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS recent_n,
                      SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS prior_n,
                      COUNT(*) AS total_n
               FROM cases
               GROUP BY company""",
            (cutoff_recent, cutoff_baseline, cutoff_recent),
        ).fetchall()
    out = []
    for r in rows:
        recent_n = r["recent_n"] or 0
        prior_n = r["prior_n"] or 0
        if recent_n < 2:
            continue
        # 베이스라인 대비 추세
        ratio = (recent_n / max(prior_n / 12, 0.5))   # prior 12주 평균 vs 최근 1주
        severity = "high" if recent_n >= 4 or ratio >= 4 else "medium"
        out.append({
            "company": r["company"],
            "recent_7d": recent_n,
            "prior_90d": prior_n,
            "ratio": round(ratio, 2),
            "severity": severity,
        })
    out.sort(key=lambda x: -x["recent_7d"])
    return {"spikes": out, "as_of": datetime.now().isoformat(timespec="seconds")}


@router.get("/salary-benchmark")
def salary_benchmark(industry: str | None = None, region: str | None = None) -> dict:
    """체불사업주 명단의 평균 체불액으로 산업×지역 기준선 산출 (보조 지표).
    실 임금 분포는 NPS·통계청 데이터로 보강 가능 (확장).
    """
    sql = "SELECT industry, region, COUNT(*) AS n, AVG(amount) AS avg_amt, MIN(amount) AS min_amt, MAX(amount) AS max_amt FROM defaulters WHERE 1=1"
    args: list = []
    if industry:
        sql += " AND industry = ?"; args.append(industry)
    if region:
        sql += " AND region = ?"; args.append(region)
    sql += " GROUP BY industry, region ORDER BY avg_amt DESC LIMIT 30"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
        # NPS에서 평균보수 비교 (있을 때)
        nps_rows = c.execute(
            "SELECT industry, AVG(avg_pay) AS avg_pay, COUNT(*) AS n FROM nps_workplaces "
            "WHERE avg_pay > 0 GROUP BY industry HAVING n >= 5 ORDER BY n DESC LIMIT 30"
        ).fetchall()
    return {
        "defaulter_avg": [dict(r) for r in rows],
        "nps_industry_avg_pay": [dict(r) for r in nps_rows],
        "note": "체불액 평균은 명단 등재 사업장 기준이라 모집단 평균보다 큼. NPS 평균보수와 결합 권장.",
    }


@router.get("/whistleblower-guide")
def whistleblower_guide() -> dict:
    """제보자 보호 가이드 — 호주·싱가포르·EU 모범사례 종합."""
    return {
        "principles": [
            "익명 신고 우선 — 본인 신원이 사업주에 노출되지 않도록 시스템이 자동 보호",
            "보복 금지는 법으로 보장 (근로기준법 §104, 근로자 보호법 등)",
            "신고 시점 입증 — 시스템이 sha256 해시 + 시각 기록",
            "다중 신고자 신뢰도 누적 — 1건은 약하지만 3명 이상 누적 시 강력",
        ],
        "do": [
            "임금명세서·통장 입금내역·근로계약서 사진을 시스템 신고 시 첨부",
            "사업주와의 카톡·문자 캡처 (날짜·시각 보이게)",
            "출퇴근 기록 (지문·카드·앱 로그) 본인 화면 스크린샷",
            "체불액 자동계산기로 미지급 추정액 산출 후 진정서 자동 첨부",
        ],
        "dont": [
            "신고 사실을 사업주·동료에게 알리지 않기 (보복 위험)",
            "사업장 컴퓨터·네트워크에서 신고 페이지 접속 금지 (감시 가능)",
            "원본 자료 삭제 금지 — 사진/PDF 원본 보관 필수",
        ],
        "channels": [
            {"name": "고용노동부 1350 종합상담",     "url": "https://www.moel.go.kr",     "desc": "전화·온라인 진정 (공식)"},
            {"name": "근로복지공단 임금채권보장",     "url": "https://total.kcomwel.or.kr","desc": "체불 시 정부가 우선 지급, 사업주에 구상 (소액체당금)"},
            {"name": "노동위원회 부당해고 구제신청",  "url": "https://www.nlrc.go.kr",      "desc": "해고 분쟁 시"},
            {"name": "법률구조공단 무료 상담",        "url": "https://www.klac.or.kr",      "desc": "월 평균임금 400만원 미만 무료 변호"},
            {"name": "WageGuard (본 시스템)",          "url": "/cases",                       "desc": "신고+증빙 → 자동 진정서 양식 + 신뢰도 누적"},
        ],
        "anti_retaliation_law": [
            "근로기준법 §104(2): 신고를 이유로 한 해고·불이익 처분 금지",
            "위반 시 사업주 형사처벌 (3년 이하 징역 또는 3천만원 이하 벌금)",
        ],
    }

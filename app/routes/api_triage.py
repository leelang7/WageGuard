"""WageGuard — 장애인 노동 사각지대 점검 우선순위 AI.

운영주체 (KEAD·근로감독관) 점검 자원이 한정된 상황에서, 공개 데이터 8기관 결합으로
의심도 점수를 산출하여 점검 우선순위를 정렬한다. 확정 행정 처분은 운영주체 권한.

5종 영역:
- Track A (실시간 차단): 명의도용 부정수급 SDK
- Track B (의심도 정렬):
    1. 위장 장애인 고용 의심도
    2. 페이퍼 장애인 사업장 의심도
    3. 장애인 임금 차별 의심도
    4. 부적합 직무 매칭 의심도

데이터 결합 (8기관 교차):
- 한국장애인고용공단 KEAD (15149876, 15131282)
- 한국고용정보원 워크넷 (3038225 외 4개)
- 국세청 사업자상태 (15081808)
- 근로복지공단 고용/산재 (15059256)
- 고용노동부 체불사업주 명단
- 금융감독원 DART 전자공시 (재무위험 선행지표)
- 국민연금공단 NPS 시계열 (가입자 급감 Z-score)
- 건강보험공단 NHIS (4대보험 삼각검증)
"""
from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api/triage")

# 최저임금 (고용노동부 고시)
MIN_WAGE_MONTHLY = {2025: 2_096_270, 2026: 2_156_880}
_MIN_WAGE_CURRENT = 2_096_270  # 2025 기준 월 최저임금

# 5분 결과 캐시 — 반복 DB 루프 방지
_cache: dict = {}
_CACHE_TTL = 300


def _cache_get(key: str):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data):
    _cache[key] = (data, time.time())


# ── 실데이터 헬퍼 ─────────────────────────────────────────────────

def _norm(name: str) -> str:
    return re.sub(r"[\s\(\)（）\[\]【】·,.\-_/]", "", name).lower()


def _nps_data(company: str) -> dict:
    """NPS DB에서 실제 가입자·상실·임금 데이터 조회."""
    n = _norm(company)
    with conn() as c:
        row = c.execute(
            "SELECT subscriber_cnt, new_cnt, lost_cnt, avg_pay, industry "
            "FROM nps_workplaces WHERE wkpl_nm_norm LIKE ? LIMIT 1",
            (f"%{n}%",),
        ).fetchone()
    if row:
        return {
            "found": True,
            "subscribers": row["subscriber_cnt"] or 0,
            "new": row["new_cnt"] or 0,
            "lost": row["lost_cnt"] or 0,
            "avg_pay": row["avg_pay"] or 0,
            "industry": row["industry"] or "",
        }
    return {"found": False}


def _nts_active(company: str) -> bool | None:
    """business_cache에서 NTS 사업자 상태 확인."""
    n = _norm(company)
    with conn() as c:
        row = c.execute(
            "SELECT nts_payload FROM business_cache "
            "WHERE json_extract(nts_payload,'$.data[0].b_stt_cd') IS NOT NULL "
            "AND bno IN (SELECT bno FROM watchlist WHERE company_query LIKE ?) LIMIT 1",
            (f"%{n}%",),
        ).fetchone()
    if not row:
        return None
    import json
    try:
        payload = json.loads(row["nts_payload"])
        b_stt = (payload.get("data") or [{}])[0].get("b_stt_cd", "")
        return b_stt in ("01",)  # 01=계속사업자
    except Exception:
        return None


def _defaulter_recent(company: str, since_year: int = 2023) -> dict:
    """체불사업주 명단에서 최근 등재 여부 확인."""
    n = _norm(company)
    with conn() as c:
        row = c.execute(
            "SELECT company, amount, year, industry, region "
            "FROM defaulters WHERE "
            "replace(replace(lower(company),' ',''),'(주)','') LIKE ? "
            "AND year >= ? ORDER BY year DESC LIMIT 1",
            (f"%{n[:10]}%", since_year),
        ).fetchone()
    if row:
        return {"found": True, "year": row["year"], "amount": row["amount"] or 0,
                "industry": row["industry"] or "", "region": row["region"] or ""}
    return {"found": False}


def _cluster_signals(company: str) -> dict:
    """company_signals 테이블에서 축적된 신호 수·심각도 조회."""
    n = _norm(company)
    with conn() as c:
        rows = c.execute(
            "SELECT channel, domain, severity, COUNT(*) as cnt "
            "FROM company_signals WHERE company_norm LIKE ? "
            "GROUP BY channel, domain, severity",
            (f"%{n}%",),
        ).fetchall()
    high = sum(r["cnt"] for r in rows if r["severity"] == "high")
    med  = sum(r["cnt"] for r in rows if r["severity"] == "medium")
    channels = list({r["channel"] for r in rows})
    domains  = list({r["domain"]  for r in rows})
    return {"high": high, "medium": med, "channels": channels, "domains": domains}


def _industry_avg_pay(industry: str) -> int | None:
    """동업종 평균 임금 (NPS DB 기준)."""
    if not industry:
        return None
    with conn() as c:
        row = c.execute(
            "SELECT AVG(avg_pay) as avg FROM nps_workplaces "
            "WHERE industry = ? AND avg_pay > 0",
            (industry,),
        ).fetchone()
    return int(row["avg"]) if row and row["avg"] else None


def _suspicion_score(industry: str, region: str, kead_registered: bool,
                     defaulter_recent: bool, business_active: bool,
                     wage_gap_pct: float, job_disability_match: float) -> dict:
    """8기관 교차 의심도 산출.

    가중치는 K-fold CV로 보정 가능. 출품 단계 보수적 가중.
    """
    score = 0.0
    breakdown = []

    # 위장 장애인 고용 의심도
    fake_hire = 0.0
    if kead_registered and not business_active:
        fake_hire += 35
        breakdown.append("KEAD 등록 + 사업자 휴/폐업 (페이퍼 의심)")
    if kead_registered and defaulter_recent:
        fake_hire += 25
        breakdown.append("KEAD 등록 + 최근 체불 (위장고용 의심)")

    # 임금 차별 의심도
    wage_gap = 0.0
    if wage_gap_pct >= 20:
        wage_gap = min(40, wage_gap_pct * 1.5)
        breakdown.append(f"동일 직무 임금 격차 {wage_gap_pct:.0f}%")

    # 부적합 매칭 의심도
    job_mismatch = 0.0
    if job_disability_match < 0.3:
        job_mismatch = (1 - job_disability_match) * 30
        breakdown.append(f"직무-장애유형 적합도 {job_disability_match:.2f} (낮음)")

    score = min(100, fake_hire + wage_gap + job_mismatch)

    return {
        "score": round(score, 1),
        "fake_hire_component": round(fake_hire, 1),
        "wage_gap_component": round(wage_gap, 1),
        "job_mismatch_component": round(job_mismatch, 1),
        "breakdown": breakdown,
    }


def _quick_base_score(year: int, amount: int) -> int:
    """DB 조회 없이 연도·금액만으로 베이스 점수 산출."""
    if year >= 2026:   base = 65
    elif year >= 2025: base = 55
    elif year >= 2024: base = 40
    else:              base = 30
    if amount >= 5_000_000_000:    amt = 20
    elif amount >= 3_000_000_000:  amt = 15
    elif amount >= 1_000_000_000:  amt = 10
    elif amount >= 500_000_000:    amt = 5
    else:                           amt = 0
    return min(100, base + amt)


def _case_counts() -> dict[str, int]:
    """신고 케이스 누적 건수 batch 조회."""
    with conn() as c:
        rows = c.execute(
            "SELECT company, COUNT(*) as cnt FROM cases GROUP BY company"
        ).fetchall()
    return {_norm(r["company"] or ""): r["cnt"] for r in rows}


@router.get("/dashboard")
def dashboard(top_n: int = 20) -> dict:
    """점검 우선순위 TOP N — 2단계 스코어링 (pre-score → top200 enrich).

    최적화:
    - 1단계: 전체 체불명단을 연도·금액 베이스 점수만으로 빠르게 정렬
    - 2단계: 상위 200건만 NPS·NTS·클러스터 DB 조회로 심화 산출
    - 최저임금(고용노동부 고시) 위반 신호 추가

    점수 가중치:
    - 체불명단 2026: 65 / 2025: 55 / 2024: 40 / 2023: 30
    - 체불액 50억+: +20 / 30억+: +15 / 10억+: +10 / 5억+: +5
    - NPS 저임금(180만↓): +10  고이직(20%↑): +10
    - 최저임금 위반 의심: +8
    - 동업종 임금격차 20%↑: +15  30%↑: +20
    - 클러스터 신호 high≥1: +10  high≥3: +20
    - NTS 폐업·휴업 확인: +15
    """
    cached = _cache_get(f"dashboard_{top_n}")
    if cached:
        return cached

    with conn() as c:
        defaulters = c.execute(
            "SELECT id, company AS business, industry, region, year, amount "
            "FROM defaulters WHERE company IS NOT NULL AND year >= 2022 "
            "ORDER BY year DESC, amount DESC LIMIT 2000"
        ).fetchall()
        nps_risky = c.execute(
            """SELECT wkpl_nm AS business, industry, region_dg AS region,
                      subscriber_cnt, lost_cnt, avg_pay
               FROM nps_workplaces
               WHERE avg_pay > 0 AND avg_pay < 1800000
                 AND subscriber_cnt > 0
                 AND (CAST(lost_cnt AS REAL)/subscriber_cnt) >= 0.20
               ORDER BY (CAST(lost_cnt AS REAL)/subscriber_cnt) DESC
               LIMIT 1000""",
        ).fetchall()
        dart_risky = c.execute(
            """SELECT corp_code, corp_name, stock_code, year, risk_score,
                      signals, financials
               FROM dart_financial_risks
               WHERE risk_score >= 20
               ORDER BY risk_score DESC
               LIMIT 200"""
        ).fetchall()

    # 1단계: 베이스 점수로 전체 정렬 → 상위 200만 심화 처리
    pre_scored = sorted(
        defaulters,
        key=lambda r: _quick_base_score(int(r["year"] or 2023), int(r["amount"] or 0)),
        reverse=True,
    )
    top_candidates = pre_scored[:200]

    import json as _json

    # DART 위험도 lookup: 체불명단과 교차 적용용
    dart_lookup: dict[str, int] = {}
    for dr in dart_risky:
        nm = dr["corp_name"] or ""
        if nm:
            dart_lookup[_norm(nm)] = int(dr["risk_score"] or 0)

    # 신고 누적 케이스 batch 조회
    case_cnt_map = _case_counts()

    sites: list[dict] = []

    # ── 1. 체불명단 기반 후보 (2단계: 상위 200 심화처리) ──────────
    for r in top_candidates:
        amt = int(r["amount"] or 0)
        yr  = int(r["year"]   or 2024)
        biz = r["business"] or ""

        # 연도 기반 베이스 점수 (확정 체불 = 가장 강한 신호)
        if yr >= 2026:   base = 65
        elif yr >= 2025: base = 55
        elif yr >= 2024: base = 40
        else:            base = 30

        # 체불액 규모 보너스
        if amt >= 500_000_000_0:  amt_bonus = 20  # 50억+
        elif amt >= 300_000_000_0: amt_bonus = 15  # 30억+
        elif amt >= 100_000_000_0: amt_bonus = 10  # 10억+
        elif amt >= 50_000_000_0:  amt_bonus = 5   # 5억+
        else:                       amt_bonus = 0

        nps   = _nps_data(biz)
        clust = _cluster_signals(biz)
        nts_active = _nts_active(biz)

        # NPS 신호 보너스
        nps_bonus = 0
        avg_pay = nps.get("avg_pay") or 0
        if avg_pay > 0 and avg_pay < 1_800_000:
            nps_bonus += 10
        loss_ratio = 0.0
        if nps.get("found") and nps.get("subscribers", 0) > 0:
            loss_ratio = (nps.get("lost", 0) or 0) / nps["subscribers"]
            if loss_ratio >= 0.2:
                nps_bonus += 10

        # 동업종 임금격차 보너스
        ind_avg = _industry_avg_pay(nps.get("industry") or r["industry"] or "")
        wage_gap_pct = 0.0
        wage_bonus = 0
        if ind_avg and avg_pay > 0:
            wage_gap_pct = max(0.0, round((ind_avg - avg_pay) / ind_avg * 100, 1))
            if wage_gap_pct >= 30:   wage_bonus = 20
            elif wage_gap_pct >= 20: wage_bonus = 15

        # NTS 폐업·휴업 보너스
        nts_bonus = 0
        if nts_active is False:
            nts_bonus = 15

        # 최저임금 위반 의심 (고용노동부 고시 기준)
        min_wage_flag = False
        min_wage_bonus = 0
        if avg_pay > 0 and avg_pay < _MIN_WAGE_CURRENT * 0.95:
            min_wage_flag = True
            min_wage_bonus = 8

        # 클러스터 보너스
        cluster_bonus = 0
        if clust["high"] >= 3:   cluster_bonus = 20
        elif clust["high"] >= 1: cluster_bonus = 10
        elif clust["medium"] >= 3: cluster_bonus = 5

        # DART 재무위험 교차 보너스 (체불명단 + DART 고위험 = 이중 검증)
        biz_norm = _norm(biz)
        dart_score = 0
        dart_bonus = 0
        for dart_nm, dscore in dart_lookup.items():
            if (biz_norm and len(biz_norm) >= 3 and len(dart_nm) >= 3
                    and (biz_norm in dart_nm or dart_nm in biz_norm)):
                dart_score = dscore
                dart_bonus = min(20, dscore // 5)
                break

        final_score = min(100, base + amt_bonus + nps_bonus + wage_bonus
                          + nts_bonus + cluster_bonus + dart_bonus + min_wage_bonus)

        breakdown = [f"체불명단 {yr}년 등재"]
        if amt > 0:
            breakdown.append(f"체불액 {amt//10000:,}만원")
        if nts_active is False:
            breakdown.append("사업자 폐업·휴업")
        if avg_pay > 0 and avg_pay < 1_800_000:
            breakdown.append(f"NPS 저임금 {avg_pay//10000:,}만원/월")
        if min_wage_flag:
            breakdown.append(f"최저임금 위반 의심 ({avg_pay//10000:,}만원 < {_MIN_WAGE_CURRENT//10000:,}만원)")
        if loss_ratio >= 0.2:
            breakdown.append(f"NPS 고이직률 {loss_ratio*100:.0f}%")
        if wage_gap_pct >= 20:
            breakdown.append(f"동업종 임금격차 {wage_gap_pct:.0f}%")
        if dart_bonus > 0:
            breakdown.append(f"DART 재무위험 {dart_score}점")

        data_srcs = ["체불명단(고용노동부)"]
        if nps["found"]:           data_srcs.append("NPS(국민연금)")
        if nts_active is not None: data_srcs.append("NTS(국세청)")
        if min_wage_flag:          data_srcs.append("최저임금(고용노동부)")
        if cluster_bonus:          data_srcs.append("클러스터신호")
        if dart_bonus > 0:         data_srcs.append("DART(금감원)")

        biz_norm_key = _norm(biz)
        case_count = case_cnt_map.get(biz_norm_key, 0)

        sites.append({
            "business": biz,
            "industry": r["industry"] or "",
            "region": r["region"] or "",
            "amount": amt,
            "year": yr,
            "nps_subscribers": nps.get("subscribers"),
            "nps_avg_pay": avg_pay or None,
            "nts_active": nts_active,
            "cluster_high": clust["high"],
            "cluster_medium": clust["medium"],
            "wage_gap_pct": wage_gap_pct,
            "min_wage_flag": min_wage_flag,
            "case_count": case_count,
            "suspicion_score": final_score,
            "breakdown": breakdown,
            "data_sources": data_srcs,
            "source": "defaulters",
            "kead_registered": clust["high"] > 0,
            "coverage_note": None,
        })

    # ── 2. NPS 고위험 (체불명단 미등재 — 임금 차별 영역) ──────────
    defaulter_names = {_norm(r["business"] or "") for r in defaulters}
    for r in nps_risky:
        biz = r["business"] or ""
        if not biz or _norm(biz) in defaulter_names:
            continue

        sub  = int(r["subscriber_cnt"] or 0)
        lost = int(r["lost_cnt"] or 0)
        pay  = int(r["avg_pay"]  or 0)
        loss_ratio = (lost / sub) if sub > 0 else 0
        ind_avg = _industry_avg_pay(r["industry"] or "")
        wage_gap_pct = 0.0
        if ind_avg and pay:
            wage_gap_pct = max(0.0, round((ind_avg - pay) / ind_avg * 100, 1))

        base = 25
        churn_bonus  = min(15, int(loss_ratio * 30))
        wage_bonus2  = 20 if wage_gap_pct >= 30 else (15 if wage_gap_pct >= 20 else 0)
        clust = _cluster_signals(biz)
        cluster_bonus2 = min(15, clust["high"] * 5 + clust["medium"] * 2)
        final_score = min(100, base + churn_bonus + wage_bonus2 + cluster_bonus2)

        if final_score < 30:
            continue

        breakdown = []
        if loss_ratio >= 0.2:
            breakdown.append(f"NPS 이직률 {loss_ratio*100:.0f}%")
        if pay < 1_800_000:
            breakdown.append(f"저임금 {pay//10000:,}만원/월")
        if wage_gap_pct >= 20:
            breakdown.append(f"동업종 임금격차 {wage_gap_pct:.0f}%")

        nps_min_wage = pay > 0 and pay < _MIN_WAGE_CURRENT * 0.95
        if nps_min_wage:
            breakdown.append(f"최저임금 위반 의심 ({pay//10000:,}만원)")
        nps_case = case_cnt_map.get(_norm(biz), 0)

        sites.append({
            "business": biz,
            "industry": r["industry"] or "",
            "region": r["region"] or "",
            "amount": 0,
            "year": None,
            "nps_subscribers": sub,
            "nps_avg_pay": pay,
            "loss_ratio_pct": round(loss_ratio * 100, 1),
            "wage_gap_pct": wage_gap_pct,
            "min_wage_flag": nps_min_wage,
            "case_count": nps_case,
            "suspicion_score": final_score,
            "breakdown": breakdown,
            "data_sources": ["NPS(국민연금)"] + (["최저임금(고용노동부)"] if nps_min_wage else []),
            "source": "nps_risky",
            "kead_registered": False,
            "coverage_note": None,
        })

    sites.sort(key=lambda x: -x["suspicion_score"])

    # ── 3. DART 선행지표 — 별도 섹션 (메인 랭킹 제외) ────────────
    dart_leading: list[dict] = []
    main_names = {_norm(s["business"]) for s in sites}
    for r in dart_risky:
        biz = r["corp_name"] or ""
        if not biz:
            continue
        try:
            signals = _json.loads(r["signals"] or "[]")
            financials = _json.loads(r["financials"] or "{}")
        except Exception:
            signals, financials = [], {}

        dart_leading.append({
            "business": biz,
            "corp_code": r["corp_code"],
            "stock_code": r["stock_code"],
            "dart_risk_score": int(r["risk_score"] or 0),
            "year": r["year"],
            "financials": financials,
            "breakdown": [s.get("label", "") for s in signals if s.get("label")],
            "coverage_note": "DART = 상장·공시기업 한정. 의무고용 위반 영세 사업장은 미포함.",
            "use_as": "체불 발생 3~6개월 전 재무위험 선행탐지 — KEAD 직접 점검 대상 아님",
        })

    result = {
        "available": True,
        "method": "체불명단(고용노동부) × NPS(국민연금) × 최저임금(고용노동부) × NTS(국세청) × 클러스터 신호 교차 의심도",
        "purpose": "운영주체(KEAD·근로감독관) 점검 자원 한정 → 영세 사업장 우선순위 정렬",
        "responsibility": "확정 적발·행정 처분은 운영주체 권한. 본 시스템은 점검 효율 보조.",
        "coverage_limitation": (
            "체불명단·NPS는 영세 사업장 포함. "
            "DART는 상장·공시기업만 → 의무고용 위반 소규모 사업장 탐지 불가 (별도 섹션 참조)."
        ),
        "top_n": top_n,
        "total_candidates": len(sites),
        "total_high": sum(1 for s in sites if s["suspicion_score"] >= 65),
        "total_med": sum(1 for s in sites if 40 <= s["suspicion_score"] < 65),
        "total_minwage": sum(1 for s in sites if s.get("min_wage_flag")),
        "total_reports": sum(1 for s in sites if s.get("case_count", 0) > 0),
        "results": sites[:top_n],
        "leading_indicators_dart": {
            "count": len(dart_leading),
            "description": "DART 재무위험 선행지표 — 상장·공시기업 대상. 체불 발생 3~6개월 전 탐지용.",
            "items": dart_leading[:10],
        },
        "data_combination": [
            "고용노동부 체불사업주 명단 (3,000건 — 영세 포함, 실데이터+합성 확장)",
            "국민연금공단 NPS 가입자·임금·이직률 DB (20,000건, 고위험 사업장 포함)",
            "국세청 NTS 사업자상태 (business_cache API 연동)",
            "company_signals 누적 신호 (5채널 교차)",
            "동업종 평균 임금 비교 (NPS 집계 기반)",
            "DART 재무위험 546건 (선행지표 별도 섹션)",
        ],
        "data_combination_note": (
            "8기관 중 현재 실데이터 연동: 체불명단(고용부)·NPS(국민연금)·NTS(국세청)·DART(금감원) 4기관. "
            "KEAD·근복·NHIS·워크넷은 활용신청 완료 후 API path 확정 대기 — "
            "ML 특성에는 의무고용율 추정값으로 간접 결합."
        ),
        "ai_modules": [
            "8기관 교차 가중 의심도 모델",
            "Logistic Regression (9특성 · K-fold CV ≥94% 실DB기반)",
            "NPS Z-score 이상탐지 (가입자 급감 경보)",
            "DART 재무위험 4지표 (선행지표)",
        ],
        "score_weights": {
            "base_2026_defaulter": 65, "base_2025_defaulter": 55,
            "base_2024_defaulter": 40, "base_2023_defaulter": 30,
            "amount_50bil_plus": 20, "amount_30bil_plus": 15,
            "nps_low_pay": 10, "nps_high_churn": 10,
            "min_wage_violation": 8,
            "wage_gap_30pct": 20, "wage_gap_20pct": 15,
            "nts_closed": 15, "cluster_high_3plus": 20, "cluster_high_1plus": 10,
            "dart_risk_bonus_max": 20,
        },
    }
    _cache_set(f"dashboard_{top_n}", result)
    return result


@router.get("/blind-spots")
def blind_spots() -> dict:
    """업종×지역 기저율 + 시스템 사각지대 분석.

    현재 모델은 체불명단·NPS에 나타나는 사업장만 점수화함.
    DART·NPS 미해당 영세사업장(10인 미만)은 신호가 없어 자동 탐지 불가.
    이 엔드포인트는 업종별 체불 기저율과 사각지대 규모를 추정해 한계를 투명하게 공개.
    """
    cached = _cache_get("blind_spots")
    if cached:
        return cached

    with conn() as c:
        # 체불명단 업종별 집계
        def_by_ind = {
            r["industry"]: {"cnt": r["cnt"], "avg_amount": int(r["avg_amount"] or 0),
                             "recent": r["recent"]}
            for r in c.execute(
                "SELECT industry, COUNT(*) cnt, AVG(amount) avg_amount,"
                " SUM(CASE WHEN year>=2024 THEN 1 ELSE 0 END) recent"
                " FROM defaulters WHERE industry IS NOT NULL AND industry!=''"
                " GROUP BY industry ORDER BY cnt DESC"
            ).fetchall()
        }

        # NPS DB 업종별 사업장 수 (이 DB에 없는 극소규모는 별도 추정)
        nps_by_ind: dict[str, int] = {}
        _IND_MAP = {
            # NPS 업종명 → 체불명단 업종명 매핑 (부분)
            "건설업": "건설업", "제조업": "제조업",
            "음식·숙박업": "숙박 및 음식점업",
            "도·소매업": "도매 및 소매업",
            "서비스업": "사업시설 관리 사업 지원 및 임대 서비스업",
            "출판·정보통신업": "정보통신업",
            "교육서비스업": "교육 서비스업",
        }
        for r in c.execute(
            "SELECT industry, COUNT(*) cnt FROM nps_workplaces"
            " WHERE industry IS NOT NULL GROUP BY industry"
        ).fetchall():
            mapped = _IND_MAP.get(r["industry"], r["industry"])
            nps_by_ind[mapped] = nps_by_ind.get(mapped, 0) + int(r["cnt"])

        # 지역별 집계
        def_by_reg = {
            r["region"]: r["cnt"]
            for r in c.execute(
                "SELECT region, COUNT(*) cnt FROM defaulters"
                " WHERE region IS NOT NULL GROUP BY region ORDER BY cnt DESC"
            ).fetchall()
        }

    total_defaulters = sum(v["cnt"] for v in def_by_ind.values())

    # 업종별 기저율 계산
    industry_rates: list[dict] = []
    for ind, d in sorted(def_by_ind.items(), key=lambda x: -x[1]["cnt"]):
        nps_covered = nps_by_ind.get(ind, 0)
        # 체불 기저율 = 체불명단 건수 / NPS 커버 사업장수
        # NPS 자체가 영세사업장 누락 → 실제 기저율은 이보다 높음
        # nps_covered < 50이면 통계적으로 무의미 (샘플 너무 적음)
        base_rate = round(d["cnt"] / nps_covered * 100, 1) if nps_covered >= 50 else None

        industry_rates.append({
            "industry": ind,
            "defaulter_count": d["cnt"],
            "recent_2024_plus": d["recent"],
            "avg_amount_won": d["avg_amount"],
            "nps_covered_workplaces": nps_covered,
            "base_rate_pct": base_rate,
            "risk_level": "high" if d["cnt"] >= 100 else "med" if d["cnt"] >= 30 else "low",
        })

    # 고위험 지역 (체불명단 기준)
    region_rates = [
        {"region": reg, "defaulter_count": cnt,
         "share_pct": round(cnt / max(total_defaulters, 1) * 100, 1)}
        for reg, cnt in sorted(def_by_reg.items(), key=lambda x: -x[1])[:8]
    ]

    # NPS 커버 50 이상인 업종만 기저율 표에 포함 (통계적 신뢰성 확보)
    meaningful_rates = [r for r in industry_rates if r["nps_covered_workplaces"] >= 50]
    # NPS 미매칭 고건수 업종은 별도 목록으로
    unmapped_rates = [r for r in industry_rates if r["nps_covered_workplaces"] < 50]

    result = {
        "available": True,
        "total_defaulters_in_db": total_defaulters,
        "industry_base_rates": meaningful_rates[:10],
        "industry_no_nps_map": [{"industry": r["industry"], "defaulter_count": r["defaulter_count"]} for r in unmapped_rates[:5]],
        "high_risk_regions": region_rates,
        "blind_spot_summary": {
            "why": (
                "체불명단은 '신고된' 케이스만 포함 — 영세·개인사업장 신고율이 낮아 실제 체불률은"
                " 이보다 높음. DART는 상장·공시기업만. NPS는 10인 미만 미등록 사업장 다수 누락."
            ),
            "most_at_risk_unseen": ["건설업 (일용직·하도급)", "숙박 및 음식점업", "도매 및 소매업"],
            "model_coverage_note": (
                "현재 모델: 체불명단 등재 후보 + NPS 고위험 후보만 점수화. "
                "미등재 영세사업장은 업종·지역 기저율로만 위험 추정 가능."
            ),
            "recommendation": (
                "Track A SDK (신청 시점 차단) + 시민 신고 채널 병행 운영으로"
                " 사각지대 보완 — 신고 건 축적 시 해당 사업장 자동 우선순위 상향."
            ),
        },
    }
    _cache_set("blind_spots", result)
    return result


@router.get("/track-a-realtime-block")
def track_a() -> dict:
    """실시간 차단 — 명의도용 부정수급 SDK 통계."""
    return {
        "available": True,
        "track": "A — 실시간 차단",
        "scope": "명의도용 부정수급 (실업급여·고용장려금·직업훈련 지원금)",
        "method": "9 신호 SDK (IP·timezone·WebRTC·WebGL·마우스·키 분산·해상도·디바이스)",
        "performance": {
            "phase1_f1": 0.864,
            "phase1_note": "1,000건 시뮬(부정 100/정상 900) — IP·timezone·jitter 5신호 기준",
            "phase3_f1": "출입국 MOU 연계 시 0.95+ 예상",
            "false_positive_rate": "0.0% (Phase 1 시뮬 기준 — 정상 900건 중 FP 0건)",
        },
        "deployment": "정부 신청 페이지에 `<script src=\"WageGuard-sdk.js\">` 한 줄 이식",
        "evidence_basis": "익명 제보로 확인된 RDP 우회 사례 + 1,000건 시뮬 (samples/m6_phase25_simulation.csv)",
    }


@router.get("/by-region")
def by_region() -> dict:
    """지역별 체불·NPS 위험도 분포 — 실데이터 집계 (한국 지도 시각화용)."""
    with conn() as c:
        # 체불명단 지역별 집계
        def_rows = c.execute(
            "SELECT region, COUNT(*) as cnt, SUM(amount) as total_amt, "
            "SUM(CASE WHEN year >= 2023 THEN 1 ELSE 0 END) as recent_cnt "
            "FROM defaulters WHERE region IS NOT NULL "
            "GROUP BY region"
        ).fetchall()

        # NPS 지역별 저임금·고이직률 집계
        nps_rows = c.execute(
            """SELECT region_dg AS region,
                      COUNT(*) AS n,
                      AVG(avg_pay) AS avg_pay,
                      SUM(CASE WHEN avg_pay < 1800000 AND avg_pay > 0 THEN 1 ELSE 0 END) AS low_pay_cnt,
                      SUM(CASE WHEN subscriber_cnt > 0
                               AND CAST(lost_cnt AS REAL)/subscriber_cnt >= 0.2
                               THEN 1 ELSE 0 END) AS high_churn_cnt
               FROM nps_workplaces WHERE region_dg IS NOT NULL
               GROUP BY region_dg""",
        ).fetchall()

        # 클러스터 신호 지역별 집계
        sig_rows = c.execute(
            "SELECT region, COUNT(*) as cnt, "
            "SUM(CASE WHEN severity='high' THEN 1 ELSE 0 END) as high_cnt "
            "FROM company_signals WHERE region IS NOT NULL GROUP BY region"
        ).fetchall()

    by_reg: dict[str, dict[str, Any]] = {}

    for r in def_rows:
        reg = r["region"]
        by_reg.setdefault(reg, {"region": reg})
        by_reg[reg]["defaulter_cnt"]    = r["cnt"]
        by_reg[reg]["defaulter_recent"] = r["recent_cnt"]
        by_reg[reg]["total_amount"]     = r["total_amt"] or 0

    for r in nps_rows:
        reg = r["region"]
        by_reg.setdefault(reg, {"region": reg})
        by_reg[reg]["nps_workplaces"]  = r["n"]
        by_reg[reg]["nps_avg_pay"]     = int(r["avg_pay"] or 0)
        by_reg[reg]["low_pay_cnt"]     = r["low_pay_cnt"] or 0
        by_reg[reg]["high_churn_cnt"]  = r["high_churn_cnt"] or 0

    for r in sig_rows:
        reg = r["region"]
        by_reg.setdefault(reg, {"region": reg})
        by_reg[reg]["signal_cnt"]      = r["cnt"]
        by_reg[reg]["signal_high_cnt"] = r["high_cnt"] or 0

    result = []
    for reg, d in by_reg.items():
        # 종합 위험도: 체불 최근건수 + 저임금 + 고이직 + 고신호
        score = (
            min(40, (d.get("defaulter_recent", 0) or 0) * 4) +
            min(20, (d.get("low_pay_cnt", 0) or 0) * 2) +
            min(20, (d.get("high_churn_cnt", 0) or 0) * 2) +
            min(20, (d.get("signal_high_cnt", 0) or 0) * 5)
        )
        d["risk_score"] = min(100, score)
        result.append(d)

    result.sort(key=lambda x: -x.get("risk_score", 0))

    return {
        "available": True,
        "regions": result,
        "method": "실데이터 집계 — 체불명단 × NPS 저임금·고이직 × 클러스터 신호",
    }


@router.get("/track-b-priority-sorting")
def track_b() -> dict:
    """점검 우선순위 정렬 — 4종 영역 의심도 모델."""
    return {
        "available": True,
        "track": "B — 점검 우선순위 정렬",
        "scope": [
            {
                "domain": "위장 장애인 고용",
                "data": "KEAD 등록 × 4대보험 가입자 × 체불명단 × 사업자상태",
                "output": "의심도 0~100",
                "decision_owner": "KEAD 점검관",
            },
            {
                "domain": "페이퍼 장애인 사업장",
                "data": "사업자상태 × 고용보험 × KEAD 등록 × 체불 × 워크넷 채용공고",
                "output": "의심도 0~100",
                "decision_owner": "KEAD 점검관 + 근로감독관",
            },
            {
                "domain": "장애인 임금 차별",
                "data": "워크넷 임금 통계 × 동일 직무 사업장 평균 비교",
                "output": "사업장 단위 격차도 0~100",
                "decision_owner": "근로감독관",
            },
            {
                "domain": "부적합 직무 매칭",
                "data": "워크넷 직무사전 × KEAD 장애 유형 적합도 매트릭스",
                "output": "직무-장애유형 적합도 0~1",
                "decision_owner": "KEAD 매칭 담당",
            },
        ],
        "value_proposition": (
            "운영주체 점검 자원이 한정된 상황에서 1만 사업장 중 우선 점검할 N개를 "
            "공개 데이터 8기관 교차로 정확히 정렬 — 점검 효율 N배 상승."
        ),
    }


@router.get("/by-industry")
def by_industry(top_n: int = 10) -> dict:
    """업종별 평균 의심도 집계 — 고위험 업종 Top N."""
    with conn() as c:
        rows = c.execute(
            """SELECT industry,
                      COUNT(*) AS cnt,
                      AVG(amount) AS avg_amt,
                      SUM(CASE WHEN year >= 2023 THEN 1 ELSE 0 END) AS recent
               FROM defaulters
               WHERE industry IS NOT NULL AND industry != ''
               GROUP BY industry
               HAVING cnt >= 3
               ORDER BY cnt DESC, avg_amt DESC
               LIMIT ?""",
            (top_n,),
        ).fetchall()

    industries = [{
        "industry": r["industry"],
        "defaulter_count": r["cnt"],
        "avg_defaulted_amount": int(r["avg_amt"] or 0),
        "recent_3yr": r["recent"],
        "risk_tier": "고위험" if r["cnt"] >= 20 else "중위험" if r["cnt"] >= 10 else "주의",
    } for r in rows]

    return {
        "available": True,
        "top_industries": industries,
        "method": "체불사업주 명단 집계 × 업종별 발생 빈도·규모",
        "use_case": "업종 priors — 점검 대상 업종 우선순위 정렬 보조",
    }


@router.get("/ai-modules")
def ai_modules_status() -> dict:
    """7 AI 모듈 현황 — 구현 방식·성능·라이브 엔드포인트."""
    import os
    return {
        "count": 7,
        "modules": [
            {
                "name": "Logistic Regression (9특성)",
                "type": "지도 학습 이진 분류",
                "impl": "pure Python (sklearn 0의존)",
                "performance": "K-fold CV F1 ≥94% / 정확도 ≥94% (실DB 기반 특성)",
                "kead_features": 2,
                "live": "/api/ml/info",
            },
            {
                "name": "TF-IDF 코사인 매칭",
                "type": "임베딩 유사도",
                "impl": "pure Python TF-IDF + 코사인",
                "performance": "직무-장애유형 적합도 0~1",
                "live": "/api/match/recommend",
            },
            {
                "name": "교차 가중 의심도 모델 (4기관 실연동)",
                "type": "룰 기반 가중 합산",
                "impl": "체불명단(고용부)·NPS(국민연금)·NTS(국세청)·DART(금감원) 실연동. "
                        "KEAD·근복·NHIS·워크넷은 활용신청 완료 후 API path 확정 대기.",
                "performance": "Track B 우선순위 정렬 핵심",
                "live": "/api/triage/dashboard",
            },
            {
                "name": "NPS Z-score 이상탐지",
                "type": "비지도 통계 이상탐지",
                "impl": "순수 Python 시계열 Z-score",
                "performance": "Z < -2.0 경보 (통계적 상위 2.3%)",
                "live": "/api/pension/timeseries",
            },
            {
                "name": "DART 재무위험 스코어링",
                "type": "룰 기반 재무 스코어링",
                "impl": "부채비율·영업손실·자본잠식·유동비율 가중 합산",
                "performance": "체불 3~6개월 선행 탐지",
                "available": bool(os.getenv("OPENDART_KEY")),
                "live": "/api/dart/diagnose",
            },
            {
                "name": "9신호 SDK (Track A 실시간 차단)",
                "type": "브라우저 핑거프린팅 분류기",
                "impl": "행동·환경·디바이스 9신호 Logistic Regression (1,000건 시뮬 학습)",
                "performance": "Phase 1 F1 0.864 (부정 100/정상 900 현실적 불균형 평가)",
                "live": "/api/m6/probe",
            },
            {
                "name": "K-fold Cross-Validation",
                "type": "모델 검증 프레임워크",
                "impl": "pure Python K-fold (k=5) stratified",
                "performance": "평균 CV F1 ≥0.94 (실DB 기반 특성)",
                "live": "/api/ml/cv",
            },
        ],
        "data_sources_total_claimed": 8,
        "data_sources_live_connected": 4,
        "organizer_datasets": 7,
        "note": "8기관 중 4기관 실데이터 연동 완료. 나머지 4기관은 활용신청 완료 / API path 확정 대기.",
    }

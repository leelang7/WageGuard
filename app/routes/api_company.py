"""사업장 360 통합 프로필 — 모든 모듈의 신호·데이터를 한 응답으로."""
from __future__ import annotations

import re

from fastapi import APIRouter

from ..db import conn
from .api_business import lookup_cell, search_defaulters
from .api_cluster import normalize as normalize_company

router = APIRouter(prefix="/api/company")


@router.get("/{name}")
def profile(name: str) -> dict:
    name = name.strip()
    norm = normalize_company(name)

    # 1) 체불 명단 직접 매칭
    hits = search_defaulters(name)

    # 2) 동일 대표자 다른 사업장
    related: list[dict] = []
    if hits:
        with conn() as c:
            for h in hits:
                rows = c.execute(
                    """SELECT round, company, amount, region FROM defaulters
                       WHERE name=? AND age=? AND company != ?""",
                    (h["name"], h["age"], h["company"]),
                ).fetchall()
                for r in rows:
                    related.append({
                        "operator": h["name"], "operator_age": h["age"],
                        "round": r["round"], "company": r["company"],
                        "amount": r["amount"], "region": r["region"],
                    })

    # 3) 셀 위험
    industry = hits[0]["industry"] if hits else None
    region = hits[0]["region"] if hits else None
    cell = lookup_cell(industry, region)

    # 4) 신호 클러스터
    with conn() as c:
        sigs = c.execute(
            """SELECT channel, domain, severity, source_ref, created_at
               FROM company_signals WHERE company_norm = ?
               ORDER BY id DESC""",
            (norm,),
        ).fetchall()
    sigs = [dict(s) for s in sigs]
    distinct_domains = {s["domain"] for s in sigs if s["domain"] and s["domain"] != "meta"}
    distinct_channels = {s["channel"] for s in sigs}

    # 5) 신고 케이스
    with conn() as c:
        case_rows = c.execute(
            """SELECT case_no, status, risk_score, amount_estimated, created_at,
                      reporter_name, is_anonymous
               FROM cases WHERE company = ? OR company LIKE ?
               ORDER BY id DESC LIMIT 30""",
            (name, f"%{name}%"),
        ).fetchall()
    cases = [dict(r) for r in case_rows]

    # 6) 워치리스트 등록 여부
    with conn() as c:
        watch = c.execute(
            "SELECT COUNT(*) AS n FROM watchlist WHERE company_query = ? OR company_query LIKE ?",
            (name, f"%{name}%"),
        ).fetchone()["n"]

    # 7) 신뢰도
    with conn() as c:
        trust_rows = c.execute(
            "SELECT reporter_contact, submitter_ip, created_at FROM cases WHERE company = ?",
            (name,),
        ).fetchall()
    distinct_reporters = len({r["reporter_contact"] for r in trust_rows if r["reporter_contact"]})
    distinct_days = len({(r["created_at"] or "")[:10] for r in trust_rows} - {""})
    trust_score = min(100, distinct_reporters * 30 + distinct_days * 15 + max(0, len(trust_rows) - 1) * 5)

    # 7-1) 부정당업자 매칭
    blacklist_items: list[dict] = []
    try:
        from .api_blacklist import init_table
        init_table()
        with conn() as c:
            blk = c.execute(
                "SELECT * FROM blacklist WHERE company_norm LIKE ? OR company LIKE ? LIMIT 5",
                (f"%{norm}%", f"%{name}%"),
            ).fetchall()
        blacklist_items = [dict(b) for b in blk]
    except Exception:
        pass

    # 8) NPS 로컬 색인
    with conn() as c:
        nps_rows = c.execute(
            """SELECT wkpl_nm, bzowr_rgst_no, addr, industry,
                      subscriber_cnt, new_cnt, lost_cnt, avg_pay, adpt_dt
               FROM nps_workplaces
               WHERE wkpl_nm_norm LIKE ? LIMIT 10""",
            (f"%{re.sub(r'[^a-z0-9가-힣]', '', name.lower())}%",),
        ).fetchall()
    nps = [dict(r) for r in nps_rows]

    # 9) 종합 위험 (룰)
    risk = 0
    factors = []
    if hits:
        risk += 60
        factors.append({"label": f"체불사업주 명단 등재 {len(hits)}건", "weight": 60})
    if cell and cell.get("risk_score", 0) >= 70:
        risk += 15
        factors.append({"label": f"고위험 셀 {cell['industry']}/{cell['region']} {cell['risk_score']}", "weight": 15})
    if len(distinct_domains) >= 2:
        risk += 20
        factors.append({"label": f"다중 도메인 신호 ({len(distinct_domains)})", "weight": 20})
    if cases:
        if trust_score >= 60:
            risk += 25
            factors.append({"label": f"누적 신고 신뢰도 {trust_score}", "weight": 25})
        else:
            risk += 10
            factors.append({"label": f"신고 케이스 {len(cases)}건", "weight": 10})
    if len(related) >= 1:
        risk += 10
        factors.append({"label": f"동일 대표 다른 사업장 {len(related)}곳", "weight": 10})
    risk = min(100, risk)

    # 신뢰도 / 표본수 명시 (통계적 정당성)
    n_total_signals = len(sigs)
    confidence = "high" if n_total_signals >= 5 else "medium" if n_total_signals >= 2 else "low"
    sample_note = (
        f"표본 {n_total_signals}건 — {'다중 시점·다중 채널 누적' if confidence == 'high' else '소표본 (참고 보조 지표)'}"
    )

    return {
        "company": name,
        "company_norm": norm,
        "hits": hits,
        "related": related,
        "cell": cell,
        "signals": sigs[:30],
        "n_signals": n_total_signals,
        "distinct_domains": sorted(distinct_domains),
        "distinct_channels": sorted(distinct_channels),
        "cases": cases,
        "watch_count": watch,
        "trust_score": trust_score,
        "blacklist_hits": blacklist_items,
        "nps": nps,
        "risk_score": risk,
        "risk_confidence": confidence,
        "risk_sample_note": sample_note,
        "risk_factors": factors,
        "risk_disclaimer": "본 점수는 룰베이스 보조 지표입니다. 공식 행정 처분 근거가 아니며, 표본·소스에 따라 false positive 가능합니다.",
        "industry": industry,
        "region": region,
    }

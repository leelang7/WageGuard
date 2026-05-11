from __future__ import annotations

import json
import os
import time
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException

from ..db import conn

router = APIRouter(prefix="/api/business")

NTS_API = "https://api.odcloud.kr/api/nts-businessman/v1/status"


def log_call(api: str, endpoint: str, status: int, duration_ms: int, ok: bool) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO api_calls (api, endpoint, status, duration_ms, ok, called_at) VALUES (?,?,?,?,?,?)",
            (api, endpoint, status, duration_ms, int(ok), datetime.now().isoformat(timespec="seconds")),
        )


def call_nts(bno: str) -> tuple[dict | None, int, int]:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        return None, 0, 0
    t0 = time.time()
    try:
        r = requests.post(
            NTS_API,
            params={"serviceKey": key, "returnType": "JSON"},
            json={"b_no": [bno]},
            timeout=10,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("NTS", NTS_API, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, r.status_code, dt
        data = r.json()
        rows = data.get("data") or []
        return (rows[0] if rows else {}), r.status_code, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call("NTS", NTS_API, 0, dt, False)
        return None, 0, dt


def cached_business(bno: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT nts_payload, kcomwel_payload, fetched_at FROM business_cache WHERE bno = ?",
            (bno,),
        ).fetchone()
    if not row:
        return None
    return {
        "nts": json.loads(row["nts_payload"]) if row["nts_payload"] else None,
        "kcomwel": json.loads(row["kcomwel_payload"]) if row["kcomwel_payload"] else None,
        "fetched_at": row["fetched_at"],
    }


def cache_business(bno: str, nts: dict | None, kcomwel: dict | None) -> None:
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO business_cache
               (bno, nts_payload, kcomwel_payload, fetched_at) VALUES (?,?,?,?)""",
            (
                bno,
                json.dumps(nts, ensure_ascii=False) if nts else None,
                json.dumps(kcomwel, ensure_ascii=False) if kcomwel else None,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def compute_risk(nts: dict | None, defaulter_hits: list[dict], cell: dict | None) -> dict:
    """다중 신호 결합 위험점수.
    - NTS 휴/폐업 상태
    - 체불사업주 명단 직접 매칭(가장 강력)
    - (업종 × 지역) 셀 위험점수
    """
    factors = []
    score = 0

    if defaulter_hits:
        n = len(defaulter_hits)
        rounds = sorted({h["round"] for h in defaulter_hits})
        amount_total = sum(h["amount"] for h in defaulter_hits)
        score += 80 + min(20, len(rounds) * 5)
        factors.append({
            "label": f"체불사업주 명단 등록 ({n}건, {len(rounds)}회 차수)",
            "points": 80,
            "color": "#dc2626",
        })
        if len(rounds) >= 2:
            factors.append({
                "label": f"명단 재등록 (재발 위험): {', '.join(rounds)}",
                "points": 15,
                "color": "#dc2626",
            })

    if nts:
        stt = nts.get("b_stt", "")
        if stt == "폐업자":
            factors.append({"label": "국세청: 폐업 사업자", "points": 60, "color": "#dc2626"})
            score += 60
        elif stt == "휴업자":
            factors.append({"label": "국세청: 휴업 사업자", "points": 40, "color": "#f59e0b"})
            score += 40
        elif stt == "계속사업자":
            factors.append({"label": "국세청: 계속사업자 (정상)", "points": 5, "color": "#10b981"})
            score += 5
        elif not stt:
            factors.append({"label": "국세청: 사업자등록 미확인", "points": 25, "color": "#94a3b8"})
            score += 25
        if nts.get("end_dt"):
            factors.append({"label": f"폐업일 {nts['end_dt']}", "points": 15, "color": "#dc2626"})
            score += 15

    if cell:
        c_score = cell.get("risk_score", 0)
        if c_score >= 70:
            pts = 25
            color = "#dc2626"
        elif c_score >= 40:
            pts = 15
            color = "#f59e0b"
        else:
            pts = 5
            color = "#94a3b8"
        factors.append({
            "label": f"업종·지역 셀 위험도: {cell['industry']} / {cell['region']} = {c_score}점",
            "points": pts,
            "color": color,
        })
        score += pts

    if not factors:
        factors.append({"label": "데이터 부족", "points": 0, "color": "#94a3b8"})

    return {"score": min(score, 100), "factors": factors}


def search_defaulters(query: str) -> list[dict]:
    """사업장명 부분 일치로 체불 명단 검색."""
    with conn() as c:
        rows = c.execute(
            """SELECT round, name, age, company, industry, region,
                      owner_addr, company_addr, amount
               FROM defaulters
               WHERE company LIKE ?
               ORDER BY round DESC""",
            (f"%{query}%",),
        ).fetchall()
    return [dict(r) for r in rows]


def lookup_cell(industry: str | None, region: str | None) -> dict | None:
    if not industry or not region:
        return None
    with conn() as c:
        row = c.execute(
            "SELECT industry, region, risk_score, count, avg_amt FROM risk_cells WHERE industry=? AND region=?",
            (industry, region),
        ).fetchone()
    return dict(row) if row else None


def build_action_guide(score: int, defaulter_hits: list[dict]) -> dict:
    """위험점수에 따른 사용자별 행동 가이드."""
    if score >= 70 or defaulter_hits:
        return {
            "level": "high",
            "label": "🚨 고위험",
            "worker": [
                "임금명세서·근로계약서·송금내역을 즉시 사진으로 보관하세요",
                "임금이 1회라도 늦어지면 지체 없이 고용노동부(1350) 신고",
                "퇴사 의사가 있으면 임금체불 사유로 정리하면 실업급여 수급권 보호됩니다",
                "체불 시 정부의 소액체당금 제도 활용 (최대 1,000만원)",
            ],
            "supervisor": [
                "관할 근로감독관에 우선 점검 대상으로 통보",
                "동일 대표자 운영 다른 사업장도 동시 모니터링",
                "임금명세서 교부 의무 이행 여부 확인",
            ],
            "owner": [
                "긴급 자금 부족 시 근로복지공단 사업주융자 신청",
                "노무사 무료 상담 채널 안내 (https://www.nodonglaw.or.kr)",
            ],
        }
    elif score >= 40:
        return {
            "level": "medium",
            "label": "⚠ 중위험",
            "worker": [
                "월급 입금 일자·금액을 기록으로 남기는 습관",
                "임금명세서를 매월 받으세요 (의무 사항)",
                "워치리스트에 등록해 두면 상태 변화 시 알림",
            ],
            "supervisor": [
                "정기 모니터링 대상으로 분류",
            ],
            "owner": [
                "임금 지급일 사전 안내 등 신뢰 관리",
            ],
        }
    else:
        return {
            "level": "low",
            "label": "✅ 저위험",
            "worker": [
                "현재 특이 신호 없음. 일반적인 임금체불 대비 수칙만 유지",
            ],
            "supervisor": [],
            "owner": [],
        }


@router.get("/{bno}")
def lookup(bno: str) -> dict:
    if not bno.isdigit() or len(bno) != 10:
        raise HTTPException(400, "사업자번호는 숫자 10자리")

    cached = cached_business(bno)
    if cached:
        nts = cached["nts"]
        nts_ms = 0
    else:
        nts, _status, nts_ms = call_nts(bno)
        cache_business(bno, nts, None)

    risk = compute_risk(nts, defaulter_hits=[], cell=None)
    return {
        "bno": bno,
        "nts": nts,
        "nts_ms": nts_ms,
        "kcomwel_note": "이 API는 사업자번호 검색을 지원하지 않습니다 (전체 페이지네이션 dump). 추후 사업장 마스터 색인 후 매칭 예정.",
        "risk": risk,
        "guide": build_action_guide(risk["score"], []),
    }


@router.get("")
def diagnose(q: str = "", region: str | None = None) -> dict:
    """S2 통합 진단: 사업장명 검색 → 명단 매칭 + 셀 위험 + 행동 가이드."""
    if not q or len(q) < 2:
        return {"query": q, "hits": [], "risk": None, "guide": None}

    hits = search_defaulters(q)

    industry = hits[0]["industry"] if hits else None
    region_resolved = region or (hits[0]["region"] if hits else None)
    cell = lookup_cell(industry, region_resolved)

    # 동일 대표자 다른 사업장
    related: list[dict] = []
    if hits:
        with conn() as c:
            for h in hits:
                rows = c.execute(
                    """SELECT round, company, amount FROM defaulters
                       WHERE name=? AND age=? AND company != ?""",
                    (h["name"], h["age"], h["company"]),
                ).fetchall()
                for r in rows:
                    related.append({
                        "operator": h["name"],
                        "round": r["round"],
                        "company": r["company"],
                        "amount": r["amount"],
                    })

    risk = compute_risk(nts=None, defaulter_hits=hits, cell=cell)
    guide = build_action_guide(risk["score"], hits)

    cluster_info = None
    if risk["score"] >= 40:
        from .api_cluster import add_signal
        # 진단 결과의 의미적 도메인 — 명단 매칭이 있으면 pay_default, 셀 위험만이면 meta
        diag_domain = "pay_default" if hits else "meta"
        cluster_info = add_signal(
            company_raw=q,
            channel="diagnosis",
            domain=diag_domain,
            severity="high" if risk["score"] >= 70 else "medium",
            source_ref=None,
            region=region_resolved,
            industry=industry,
        )

    return {
        "query": q,
        "industry": industry,
        "region": region_resolved,
        "hits": hits,
        "related": related,
        "cell": cell,
        "risk": risk,
        "guide": guide,
        "cluster": cluster_info,
    }


@router.get("/{bno}")
def lookup(bno: str) -> dict:
    if not bno.isdigit() or len(bno) != 10:
        raise HTTPException(400, "사업자번호는 숫자 10자리")

    cached = cached_business(bno)
    if cached:
        nts = cached["nts"]
        nts_ms = 0
    else:
        nts, _status, nts_ms = call_nts(bno)
        cache_business(bno, nts, None)

    return {
        "bno": bno,
        "nts": nts,
        "nts_ms": nts_ms,
        "kcomwel_note": "이 API는 사업자번호 검색을 지원하지 않습니다 (전체 페이지네이션 dump). 추후 사업장 마스터 색인 후 매칭 예정.",
        "risk": compute_risk(nts),
    }

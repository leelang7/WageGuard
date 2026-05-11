"""실시간 라이브 검증 — 회사명 입력 → 7개 외부 API + AI 종합 SSE 스트림.

체불명단 · 국세청 · 네이버 · Google · NPS 이탈률 · DART 재무위험 · AI 종합.
NPS·DART 미등록 영세사업장은 업종 기저율 floor score 적용.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import AsyncIterator

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..db import conn
from ..events import log_event

router = APIRouter(prefix="/api/verify")

# 실DB 기반 업종별 체불 기저율 (NPS 커버 50개 이상 업종만, /api/triage/blind-spots 동일 수치)
_INDUSTRY_BASE_RATE: dict[str, float] = {
    "건설업": 5.0,
    "제조업": 4.2,
    "도매 및 소매업": 4.7,
    "숙박 및 음식점업": 2.7,
    "정보통신업": 5.4,
    "사업시설 관리 사업 지원 및 임대 서비스업": 1.2,
    "교육 서비스업": 2.4,
    "부동산업": 1.7,
}

# 회사명 키워드 → 업종 추론 (순서 중요: 더 구체적인 것 먼저)
_NAME_KW: list[tuple[str, str]] = [
    ("인테리어", "건설업"), ("철거", "건설업"), ("토목", "건설업"),
    ("건설", "건설업"), ("건축", "건설업"), ("공사", "건설업"),
    ("소프트", "정보통신업"), ("테크", "정보통신업"), ("시스템", "정보통신업"),
    ("it", "정보통신업"), ("sw", "정보통신업"),
    ("공인중개", "부동산업"), ("부동산", "부동산업"),
    ("학원", "교육 서비스업"), ("교육", "교육 서비스업"),
    ("식품", "숙박 및 음식점업"), ("푸드", "숙박 및 음식점업"),
    ("요식", "숙박 및 음식점업"), ("물산", "도매 및 소매업"),
    ("유통", "도매 및 소매업"), ("물류", "도매 및 소매업"),
    ("무역", "도매 및 소매업"), ("제조", "제조업"),
    ("공장", "제조업"),
]


def _infer_industry(company: str, signals: dict) -> str | None:
    """업종 추론: ① 체불DB 기등재 업종 → ② 회사명 키워드."""
    for m in ((signals.get("defaulter") or {}).get("matches") or []):
        ind = (m.get("industry") or "").strip()
        if ind in _INDUSTRY_BASE_RATE:
            return ind
    lower = company.lower()
    for kw, ind in _NAME_KW:
        if kw in lower:
            return ind
    return None


def _sse(event: str, data: dict) -> str:
    """SSE 메시지 포맷."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize(name: str) -> str:
    """회사명 정규화 — 괄호·점·공백·법인형태 제거."""
    return re.sub(r"[\s\(\)\.\·]", "", name).lower()


async def _check_defaulter(company: str) -> dict:
    """체불사업주 명단 매칭 — 정규화 검색 (주), 공백 등 변형 처리."""
    await asyncio.sleep(0.3)  # UX 효과
    norm = _normalize(company)
    with conn() as c:
        rows = c.execute(
            "SELECT company, year, amount, region, industry, name FROM defaulters "
            "WHERE LOWER(REPLACE(REPLACE(REPLACE(REPLACE(company,'(',''),')',''),'.',''),' ','')) "
            "LIKE ? ORDER BY year DESC LIMIT 5",
            (f"%{norm}%",),
        ).fetchall()
    matches = [{
        "company": r["company"], "year": r["year"], "amount": r["amount"],
        "region": r["region"], "industry": r["industry"], "owner": r["name"],
    } for r in rows]
    return {
        "ok": True,
        "matched": len(matches),
        "matches": matches,
    }


async def _check_nts(company: str) -> dict:
    """국세청 사업자상태 — 사업자번호 추정 어려우므로 체불 매칭에서 추출 시도."""
    await asyncio.sleep(0.5)
    norm = _normalize(company)
    with conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM defaulters "
            "WHERE LOWER(REPLACE(REPLACE(REPLACE(REPLACE(company,'(',''),')',''),'.',''),' ','')) "
            "LIKE ?",
            (f"%{norm}%",),
        ).fetchone()["n"]
    has_key = bool(os.getenv("DATA_GO_KR_KEY"))
    return {
        "ok": has_key,
        "endpoint": "data.go.kr 15081808",
        "has_key": has_key,
        "note": "사업자번호 입력 시 휴/폐업 즉시 조회 가능",
        "indirect_signal": f"체불명단 {n}건 매칭" if n else "체불명단 미매칭",
    }


async def _check_naver(company: str) -> dict:
    """네이버 검색 5채널 — 위험 키워드 매칭."""
    await asyncio.sleep(0.4)
    cid = os.getenv("NAVER_CLIENT_ID")
    csec = os.getenv("NAVER_CLIENT_SECRET")
    if not cid or not csec:
        return {"ok": False, "reason": "키 미설정"}

    risk_kw = ["체불", "노동부", "신고", "잠수", "폐업"]
    channels = ["news", "blog", "cafearticle"]
    results = {"channels": {}, "total_risk_hits": 0}
    async with httpx.AsyncClient(timeout=4.0) as cx:
        for ch in channels:
            try:
                r = await cx.get(
                    f"https://openapi.naver.com/v1/search/{ch}",
                    params={"query": company, "display": 5},
                    headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
                )
                if r.status_code == 200:
                    items = r.json().get("items", [])
                    risk_count = 0
                    for it in items:
                        title = (it.get("title") or "") + (it.get("description") or "")
                        if any(k in title for k in risk_kw):
                            risk_count += 1
                    results["channels"][ch] = {
                        "total": len(items),
                        "risk_hits": risk_count,
                    }
                    results["total_risk_hits"] += risk_count
            except Exception as e:
                results["channels"][ch] = {"error": str(e)[:80]}
    results["ok"] = True
    return results


async def _check_google_places(company: str) -> dict:
    """Google Places — 평점·리뷰."""
    await asyncio.sleep(0.4)
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        return {"ok": False, "reason": "키 미설정"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as cx:
            r = await cx.post(
                "https://places.googleapis.com/v1/places:searchText",
                json={"textQuery": company, "languageCode": "ko"},
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,places.formattedAddress",
                },
            )
            if r.status_code == 200:
                d = r.json()
                places = d.get("places", [])[:3]
                return {
                    "ok": True,
                    "matched": len(places),
                    "top": [{
                        "name": p.get("displayName", {}).get("text"),
                        "rating": p.get("rating"),
                        "review_count": p.get("userRatingCount"),
                        "address": p.get("formattedAddress"),
                    } for p in places],
                }
            return {"ok": False, "reason": f"status {r.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:120]}


async def _check_nps(company: str) -> dict:
    """NPS 가입자 현황 + 이탈률 신호."""
    await asyncio.sleep(0.2)
    from .api_pension import _local_search, _items_from_local, signals_from_enrollment
    rows = _local_search(company)
    if not rows:
        has_key = bool(os.getenv("DATA_GO_KR_KEY"))
        return {"ok": has_key, "matched": 0, "note": "로컬 NPS 데이터 없음 — API 키 있으면 라이브 호출 가능"}
    items = _items_from_local(rows)
    result = signals_from_enrollment(items)
    return {"ok": True, **result}


async def _check_dart(company: str) -> dict:
    """DART 재무위험 진단 — 체불 선행지표."""
    await asyncio.sleep(0.2)
    from .api_dart import diagnose
    result = diagnose(company, add_to_cluster=False)
    if not result.get("available"):
        return {"ok": False, "reason": result.get("reason", "DART 미조회")}
    return {"ok": True, **result}


async def _ai_synthesize(company: str, signals: dict) -> dict:
    """7신호 통합 → WageGuard 의심도 산출.

    개별 신호가 전혀 없는 영세사업장(NPS·DART 미등록 추정)은
    업종 기저율을 floor score로 적용 — 0점 = 안전 오해 방지.
    """
    await asyncio.sleep(0.2)
    score = 0.0
    reasons = []
    d = signals.get("defaulter") or {}
    if d.get("matched"):
        score += 40 + min(d["matched"], 5) * 6
        reasons.append(f"체불명단 매칭 {d['matched']}건")
        max_amount = max((m.get("amount") or 0 for m in (d.get("matches") or [])), default=0)
        if max_amount >= 500_000_000:
            score += 15
            reasons.append(f"고액 체불 {max_amount // 100_000_000}억원")
        elif max_amount >= 100_000_000:
            score += 8
            reasons.append(f"체불 {max_amount // 100_000_000}억원")
    n = signals.get("naver") or {}
    if n.get("total_risk_hits"):
        score += min(40, n["total_risk_hits"] * 4)
        reasons.append(f"네이버 위험 키워드 {n['total_risk_hits']}건")
    g = signals.get("google") or {}
    if g.get("ok") and g.get("top"):
        avg_r = sum(p.get("rating") or 0 for p in g["top"]) / max(len(g["top"]), 1)
        if avg_r and avg_r < 3.0:
            score += 15
            reasons.append(f"Google 평균 평점 낮음 {avg_r:.1f}")
    nps = signals.get("nps") or {}
    if nps.get("overall_severity") == "high":
        score += 20
        reasons.append("NPS 가입자 이탈률 이상")
    elif nps.get("overall_severity") == "medium":
        score += 10
        reasons.append("NPS 회전율 주의")
    dart = signals.get("dart") or {}
    if dart.get("ok") and dart.get("risk_score"):
        ds = dart["risk_score"]
        if ds >= 60:
            score += 25
            reasons.append(f"DART 재무위험 {ds}/100")
        elif ds >= 30:
            score += 12
            reasons.append(f"DART 재무 주의 {ds}/100")
    score = min(100, score)

    # ── 영세사업장 기저율 보정 ──────────────────────────────────────────
    # 체불DB·NPS·DART 세 소스 모두 개별 신호 없고 업종 추론 가능하면
    # 해당 업종의 DB 실측 기저율을 최소 floor score로 적용.
    # (5% 기저율 → 30점, 선형 변환: floor = base_rate * 6)
    base_rate_applied = False
    inferred_industry = None
    no_individual = (
        not d.get("matched")
        and not nps.get("matched")
        and not (dart.get("ok") and dart.get("risk_score"))
    )
    if no_individual:
        inferred_industry = _infer_industry(company, signals)
        if inferred_industry:
            base_rate = _INDUSTRY_BASE_RATE.get(inferred_industry, 0.0)
            floor = int(base_rate * 6)  # 5.0% → 30점
            if floor > score:
                score = float(floor)
                reasons.append(
                    f"{inferred_industry} 업종 기저율 {base_rate}% 적용 "
                    f"(NPS·DART 미등록 — 영세사업장 추정)"
                )
                base_rate_applied = True

    return {
        "score": round(score, 1),
        "reasons": reasons,
        "decision": "고위험" if score >= 70 else "중위험" if score >= 40 else "저위험",
        "base_rate_applied": base_rate_applied,
        "inferred_industry": inferred_industry,
    }


@router.get("/stream")
async def stream(company: str = "") -> StreamingResponse:
    """SSE 스트림 — 7단계 실시간 검증."""
    company = (company or "").strip()
    if not company:
        async def empty():
            yield _sse("error", {"reason": "company 파라미터 필요"})
        return StreamingResponse(empty(), media_type="text/event-stream")

    log_event("verify_stream", f"라이브 검증 시작 — {company}", actor="user",
              payload={"company": company})

    async def gen() -> AsyncIterator[str]:
        signals = {}
        steps = [
            ("step", {"n": 1, "label": "체불사업주 명단 매칭", "status": "running"}),
        ]
        for s in steps:
            yield _sse(*s)

        d = await _check_defaulter(company)
        signals["defaulter"] = d
        yield _sse("step", {"n": 1, "label": "체불사업주 명단 매칭", "status": "done", "result": d})

        yield _sse("step", {"n": 2, "label": "국세청 사업자상태", "status": "running"})
        nts = await _check_nts(company)
        signals["nts"] = nts
        yield _sse("step", {"n": 2, "label": "국세청 사업자상태", "status": "done", "result": nts})

        yield _sse("step", {"n": 3, "label": "네이버 검색 (3채널) 위험 키워드 매칭", "status": "running"})
        nv = await _check_naver(company)
        signals["naver"] = nv
        yield _sse("step", {"n": 3, "label": "네이버 검색 (3채널) 위험 키워드 매칭", "status": "done", "result": nv})

        yield _sse("step", {"n": 4, "label": "Google Places 평점·리뷰", "status": "running"})
        gp = await _check_google_places(company)
        signals["google"] = gp
        yield _sse("step", {"n": 4, "label": "Google Places 평점·리뷰", "status": "done", "result": gp})

        yield _sse("step", {"n": 5, "label": "NPS 국민연금 가입자 이탈률 분석", "status": "running"})
        nps = await _check_nps(company)
        signals["nps"] = nps
        yield _sse("step", {"n": 5, "label": "NPS 국민연금 가입자 이탈률 분석", "status": "done", "result": nps})

        yield _sse("step", {"n": 6, "label": "DART 재무위험 진단 (체불 선행지표)", "status": "running"})
        dart = await _check_dart(company)
        signals["dart"] = dart
        yield _sse("step", {"n": 6, "label": "DART 재무위험 진단 (체불 선행지표)", "status": "done", "result": dart})

        yield _sse("step", {"n": 7, "label": "WageGuard AI 종합 의심도 산출", "status": "running"})
        ai = await _ai_synthesize(company, signals)
        yield _sse("step", {"n": 7, "label": "WageGuard AI 종합 의심도 산출", "status": "done", "result": ai})

        yield _sse("complete", {"company": company, "signals": signals, "ai": ai})

        log_event("verify_complete", f"라이브 검증 완료 — {company} · 점수 {ai['score']}",
                  actor="system", payload={"company": company, "score": ai["score"]})

    return StreamingResponse(gen(), media_type="text/event-stream")

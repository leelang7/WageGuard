"""4대보험 삼각검증 — NPS·건강보험·채용공고 교차로 위장고용·페이퍼사업장 탐지.

삼각검증 원리:
  A. NPS 국민연금 가입자 수  (data.go.kr → nps_workplaces DB)
  B. 건강보험 직장가입자 수  (data.go.kr 건강보험공단 직장가입자 현황)
  C. 워크넷/네이버 채용공고 수 (외부 검색 신호)

불일치 패턴:
  A ≪ B       → 국민연금 납부 회피 (저임금·단시간 위장)
  A ≫ C       → 계속 이직 (채용공고 없는데 가입자 교체 반복 = 허위 고용)
  A > 0, B = 0 → NPS만 있고 건보 미가입 = 비정규 위장
  A = 0, B > 0 → 건보만 있고 NPS 미가입 = 임금 조작 가능성
  모두 = 0     → 사업자 활성이나 4대보험 전무 = 유령사업장

활용 데이터:
  - NPS: nps_workplaces 테이블 (로컬 CSV) + data.go.kr API
  - 건강보험: data.go.kr 건강보험공단 직장가입자 현황 API
             (데이터셋 ID: 15007192 — 자동승인)
  - 채용공고: api_external Naver 검색 캐시 or 직접 호출
"""
from __future__ import annotations

import math
import os
import random
import re
import time

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import conn
from .api_business import log_call
from .api_cluster import add_signal

router = APIRouter(prefix="/api/insurance-cross")

# 건강보험공단 직장가입자 현황 API
NHIS_BASE = "http://apis.data.go.kr/B551182/insuredPersonInfoService"
NHIS_ENDPOINT = f"{NHIS_BASE}/getInsuredPersonInfo"


def _data_go_key() -> str:
    return os.environ.get("DATA_GO_KR_KEY", "").strip()


def _call_nhis(params: dict) -> tuple[dict | None, int, int]:
    key = _data_go_key()
    if not key:
        return None, 0, 0
    t0 = time.time()
    try:
        r = requests.get(
            NHIS_ENDPOINT,
            params={"serviceKey": key, "_type": "json", "numOfRows": 20, **params},
            timeout=15,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("NHIS", NHIS_ENDPOINT, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, r.status_code, dt
        return r.json(), r.status_code, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call("NHIS", NHIS_ENDPOINT, 0, dt, False)
        return None, 0, dt


def _nhis_items(payload: dict) -> list[dict]:
    body = (payload or {}).get("response", {}).get("body", {})
    items = body.get("items", {})
    if isinstance(items, dict):
        item = items.get("item", [])
        return [item] if isinstance(item, dict) else (item or [])
    return []


def get_nhis_subscribers(name: str) -> tuple[int | None, str]:
    """건강보험 직장가입자 수 조회. 반환: (가입자수 or None, source)"""
    data, status, _ = _call_nhis({"wkplNm": name[:20]})
    if data and status == 200:
        items = _nhis_items(data)
        if items:
            cnt = 0
            for it in items:
                v = it.get("insrdCnt") or it.get("subscriberCnt") or 0
                try:
                    cnt = max(cnt, int(v))
                except (ValueError, TypeError):
                    pass
            return cnt, "nhis_api"
    return None, "unavailable"


def get_nps_subscribers(name: str) -> tuple[int | None, int | None, str]:
    """NPS 가입자·상실자 수 조회. DB 우선, 없으면 API."""
    norm = re.sub(r"[\s\(\)（）\[\]【】·,\.\-_/]", "", name).lower()
    with conn() as c:
        row = c.execute(
            "SELECT subscriber_cnt, lost_cnt FROM nps_workplaces WHERE wkpl_nm_norm LIKE ? LIMIT 1",
            (f"%{norm}%",),
        ).fetchone()
    if row:
        return row["subscriber_cnt"], row["lost_cnt"], "nps_local"

    # API 폴백
    key = _data_go_key()
    if not key:
        return None, None, "unavailable"
    from .api_pension import search_enrolled, _items_from_local
    items, status, _ = search_enrolled(name)
    if items:
        it = items[0]
        sub = int(it.get("jnngpCnt") or 0)
        lost = int(it.get("lossSubscbrCnt") or it.get("jnngpFrftCnt") or 0)
        return sub, lost, "nps_api"
    return None, None, "unavailable"


def get_job_posting_count(name: str) -> tuple[int, str]:
    """Naver 검색으로 해당 사업장의 활성 채용공고 수 추정."""
    # company_signals 테이블에 hiring 도메인 신호가 있으면 최근 채용 활성으로 판단
    norm = re.sub(r"[\s\(\)（）\[\]【】·,\.\-_/]", "", name).lower()
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) as cnt FROM company_signals "
            "WHERE company_norm LIKE ? AND domain = 'hiring' "
            "AND created_at >= date('now', '-30 days')",
            (f"%{norm}%",),
        ).fetchone()
    return (row["cnt"] if row else 0), "signal_cache"


def cross_validate(
    nps_sub: int | None,
    nps_lost: int | None,
    nhis_sub: int | None,
    job_cnt: int,
    company: str,
) -> dict:
    """삼각검증 불일치 → 위험 신호 산출."""
    score = 0
    signals: list[dict] = []

    nps = nps_sub or 0
    nhis = nhis_sub or 0
    lost = nps_lost or 0

    # 신호 1: NPS vs 건보 불일치
    if nps > 0 and nhis > 0:
        ratio = max(nps, nhis) / max(min(nps, nhis), 1)
        if ratio > 3:
            score += 30
            signals.append({
                "label": f"NPS 가입자 {nps}명 vs 건보 직장가입자 {nhis}명 — {ratio:.1f}배 불일치",
                "severity": "high", "domain": "insurance_mismatch",
            })
        elif ratio > 2:
            score += 15
            signals.append({
                "label": f"NPS·건보 가입자 수 {ratio:.1f}배 차이",
                "severity": "medium", "domain": "insurance_mismatch",
            })

    # 신호 2: NPS만 있고 건보 없음 (비정규 위장)
    if nps > 2 and nhis == 0 and nhis_sub is not None:
        score += 25
        signals.append({
            "label": f"NPS 가입자 {nps}명이지만 건강보험 직장가입 0명",
            "severity": "high", "domain": "insurance_mismatch",
        })

    # 신호 3: 가입자 있는데 채용공고 과잉 (허위 고용 또는 높은 이직률)
    if nps > 0 and job_cnt >= 3:
        score += 15
        signals.append({
            "label": f"최근 채용공고 {job_cnt}건인데 NPS 가입자 {nps}명 — 고회전율 의심",
            "severity": "medium", "domain": "hiring",
        })

    # 신호 4: NPS 상실률 과다 (가입자 대비 상실자)
    if nps > 0 and lost > 0:
        loss_pct = round(lost / nps * 100)
        if loss_pct >= 30:
            score += 20
            signals.append({
                "label": f"NPS 가입자 {nps}명 중 {lost}명 상실 ({loss_pct}%) — 고이직률",
                "severity": "high", "domain": "hiring",
            })

    # 신호 5: 모든 소스 0 — 유령사업장 가능성
    if nps == 0 and nhis == 0 and nps_sub is not None and nhis_sub is not None:
        score += 20
        signals.append({
            "label": "NPS·건보 가입자 모두 0 — 유령 사업장 또는 개업 직후",
            "severity": "medium", "domain": "closure",
        })

    severity = None
    if score >= 50:
        severity = "high"
    elif score >= 25:
        severity = "medium"

    return {
        "risk_score": min(score, 100),
        "severity": severity,
        "signals": signals,
        "sources": {
            "nps_subscribers": nps_sub,
            "nps_lost": nps_lost,
            "nhis_subscribers": nhis_sub,
            "job_posting_signals": job_cnt,
        },
    }


class CrossIn(BaseModel):
    company: str
    add_to_cluster: bool = True


@router.post("/scan")
def scan(inp: CrossIn) -> dict:
    """사업장명 → 4대보험 삼각검증 (NPS·건보·채용공고 교차)."""
    name = inp.company.strip()
    if len(name) < 2:
        return {"available": False, "reason": "사업장명 2자 이상 필요"}

    nps_sub, nps_lost, nps_src = get_nps_subscribers(name)
    nhis_sub, nhis_src = get_nhis_subscribers(name)
    job_cnt, job_src = get_job_posting_count(name)

    result = cross_validate(nps_sub, nps_lost, nhis_sub, job_cnt, name)
    result["sources_detail"] = {"nps": nps_src, "nhis": nhis_src, "jobs": job_src}

    if inp.add_to_cluster and result["severity"]:
        for sig in result["signals"]:
            add_signal(
                company_raw=name,
                channel="insurance_cross",
                domain=sig["domain"],
                severity=sig["severity"],
                source_ref=f"삼각검증:{sig['label'][:40]}",
            )

    if nps_sub is None and nhis_sub is None and job_cnt == 0:
        return {
            "available": False,
            "company": name,
            "method": "4대보험 삼각검증 (NPS + 건강보험 + 채용공고)",
            "reason": "실제 NPS/NHIS/채용공고 데이터가 조회되지 않았습니다. 0점으로 추정하지 않습니다.",
            "sources": result["sources"],
            "sources_detail": result["sources_detail"],
        }

    return {
        "available": True,
        "company": name,
        "method": "4대보험 삼각검증 (NPS + 건강보험 + 채용공고)",
        **result,
    }


@router.get("/scan")
def scan_get(company: str) -> dict:
    return scan(CrossIn(company=company, add_to_cluster=False))


@router.get("/demo")
def demo(company: str = "") -> dict:
    """체불 실제 사업장 기반 4대보험 삼각검증 시뮬레이션."""
    with conn() as c:
        if company:
            norm = re.sub(r"[\s\(\)\.]", "", company).lower()
            rows = c.execute(
                "SELECT company, amount, year FROM defaulters WHERE LOWER(REPLACE(REPLACE(company,'(',''),')',' ')) LIKE ? LIMIT 1",
                (f"%{norm}%",),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT company, amount, year FROM defaulters ORDER BY amount DESC LIMIT 1"
            ).fetchall()

    if not rows:
        return {"available": False, "reason": "체불명단 데이터 없음"}

    row = rows[0]
    cname = row["company"]
    amount_wan = row["amount"]
    year = row["year"] or 2023

    rng = random.Random(hash(cname) & 0xFFFF)
    base_nps = rng.randint(15, 80)
    base_nhis = int(base_nps * rng.uniform(0.85, 1.1))
    loss_rate_pct = rng.uniform(35, 65)
    job_cnt = rng.randint(0, 2)

    nps_ratio = base_nps / max(base_nhis, 1)
    discrepancy_pct = abs(base_nps - base_nhis) / max(base_nhis, 1) * 100

    signals = []
    score = 0

    if loss_rate_pct >= 30:
        signals.append({
            "label": f"NPS 상실률 {loss_rate_pct:.0f}% — 고회전율 (허위고용·체불 전조)",
            "severity": "HIGH",
            "score": 40,
        })
        score += 40

    if discrepancy_pct >= 20:
        signals.append({
            "label": f"NPS({base_nps}명) vs 건보({base_nhis}명) {discrepancy_pct:.0f}% 불일치",
            "severity": "HIGH" if discrepancy_pct >= 40 else "MED",
            "score": 30,
        })
        score += 30

    if job_cnt == 0 and base_nps >= 10:
        signals.append({
            "label": f"가입자 {base_nps}명인데 채용공고 0건 — 반복 이직 의심",
            "severity": "MED",
            "score": 20,
        })
        score += 20

    if nps_ratio < 0.5:
        signals.append({
            "label": "NPS 가입자가 건보 대비 50% 미만 — 국민연금 납부 회피",
            "severity": "HIGH",
            "score": 25,
        })
        score += 25

    score = min(score, 100)
    tier = "HIGH" if score >= 70 else ("MED" if score >= 40 else "LOW")

    return {
        "is_simulation": True,
        "sim_note": f"실제 체불사업장({cname})을 기반으로 한 삼각검증 시뮬레이션입니다. NPS/NHIS 수치는 체불 패턴 재현 시뮬 값입니다.",
        "available": True,
        "company": cname,
        "amount_wan": amount_wan,
        "detected_year": year,
        "method": "4대보험 삼각검증 (NPS + 건강보험 + 채용공고) — 시뮬레이션",
        "nps_subscribers": base_nps,
        "nhis_subscribers": base_nhis,
        "job_postings": job_cnt,
        "loss_rate_pct": round(loss_rate_pct, 1),
        "discrepancy_pct": round(discrepancy_pct, 1),
        "cross_score": score,
        "tier": tier,
        "signals": signals,
        "sources": {
            "nps": {"subscribers": base_nps, "loss_rate": round(loss_rate_pct, 1)},
            "nhis": {"subscribers": base_nhis},
            "job_postings": job_cnt,
        },
    }


@router.get("/catalog")
def catalog() -> dict:
    return {
        "method": "4대보험 삼각검증",
        "sources": [
            {"name": "국민연금 가입 현황", "id": "B552015/NpsBplcInfoInqireService", "key": "DATA_GO_KR_KEY"},
            {"name": "건강보험 직장가입자 현황", "id": "B551182/insuredPersonInfoService", "key": "DATA_GO_KR_KEY"},
            {"name": "채용공고 신호 (캐시)", "id": "company_signals:hiring", "key": "없음 (내부 DB)"},
        ],
        "detection_patterns": [
            "NPS vs 건보 가입자 수 2배↑ 불일치 → 4대보험 조작 의심",
            "NPS 가입자 있는데 건보 0 → 비정규 위장",
            "상실률 30%↑ → 고회전율 (허위고용·체불 전조)",
            "모든 소스 0 → 유령사업장",
        ],
        "nhis_key_status": bool(_data_go_key()),
    }

"""운영주체(KEAD 점검관·근로감독관) 시뮬레이터.

평가위원이 직접 운영주체 입장에서:
1. 의심도 TOP 사업장 받기
2. 점검 출동 여부 결정
3. 점검 결과 입력 (적발/무혐의)
4. 모델 가중치 자동 보정 (in-memory)

→ "시스템이 어떻게 운영주체에게 가치를 주는지" 직접 체험.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from ..db import conn
from ..events import log_event

router = APIRouter(prefix="/api/operator")


# 가중치는 in-memory (시뮬용 — 운영 환경에서는 별도 DB 컬럼)
_FEEDBACK_WEIGHTS = {
    "kead_register_with_closure": 1.0,
    "kead_with_recent_default": 1.0,
    "wage_gap_high": 1.0,
    "job_mismatch": 1.0,
}


def _inspection_log_db() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT business_id, business_name, verdict, signals, suspicion_score, inspector, created_at "
            "FROM inspections ORDER BY id DESC LIMIT 200"
        ).fetchall()
    out = []
    for r in rows:
        try:
            sigs = json.loads(r["signals"]) if r["signals"] else []
        except Exception:
            sigs = []
        out.append({
            "business_id": r["business_id"],
            "business_name": r["business_name"],
            "verdict": r["verdict"],
            "signals": sigs,
            "suspicion_score": r["suspicion_score"],
            "inspector": r["inspector"],
            "created_at": r["created_at"],
        })
    return out


@router.get("/queue")
def queue(top_n: int = 10) -> dict:
    """운영주체 점검 큐 — 의심도 TOP N + 점검 시뮬용 컨텍스트."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, company AS business, industry, region, year, amount FROM defaulters "
            "WHERE company IS NOT NULL "
            "ORDER BY amount DESC LIMIT 100"
        ).fetchall()

    queue = []
    for r in rows[:top_n * 2]:
        rid = int(r["id"])
        amt = int(r["amount"] or 0)
        yr = int(r["year"] or 2024)

        kead_registered = (rid * 31 % 100) < 35
        years_back = max(0, 2026 - yr)
        business_active = ((rid * 17 + years_back * 3) % 100) >= (30 + years_back * 5)
        defaulter_recent = yr >= 2025
        wage_gap = min(45, (amt / 1_000_000_000) * 30) if amt else 0
        job_match = ((rid * 7 % 100) / 100.0) * 0.8 + 0.1

        # 점수 산출 (가중치 적용)
        score = 0.0
        signals = []
        if kead_registered and not business_active:
            score += 35 * _FEEDBACK_WEIGHTS["kead_register_with_closure"]
            signals.append("kead_register_with_closure")
        if kead_registered and defaulter_recent:
            score += 25 * _FEEDBACK_WEIGHTS["kead_with_recent_default"]
            signals.append("kead_with_recent_default")
        if wage_gap >= 20:
            score += min(40, wage_gap * 1.5) * _FEEDBACK_WEIGHTS["wage_gap_high"]
            signals.append("wage_gap_high")
        if job_match < 0.3:
            score += (1 - job_match) * 30 * _FEEDBACK_WEIGHTS["job_mismatch"]
            signals.append("job_mismatch")

        if score < 25:
            continue

        already_inspected = False  # 채워짐 아래

        queue.append({
            "business_id": rid,
            "business": r["business"],
            "industry": r["industry"],
            "region": r["region"],
            "amount": amt,
            "kead_registered": kead_registered,
            "business_active": business_active,
            "defaulter_recent": defaulter_recent,
            "wage_gap_pct": round(wage_gap, 1),
            "job_disability_match": round(job_match, 2),
            "suspicion_score": round(min(100, score), 1),
            "active_signals": signals,
            "already_inspected": already_inspected,
        })

    # DB에서 이미 점검된 ID 조회
    with conn() as c:
        inspected_ids = {r[0] for r in c.execute("SELECT business_id FROM inspections").fetchall()}
    for q in queue:
        q["already_inspected"] = q["business_id"] in inspected_ids

    queue.sort(key=lambda x: -x["suspicion_score"])
    inspections_done = len(inspected_ids)
    return {
        "available": True,
        "queue": queue[:top_n],
        "current_weights": dict(_FEEDBACK_WEIGHTS),
        "inspections_done": inspections_done,
    }


@router.post("/inspect")
def inspect(payload: dict) -> dict:
    """점검 결과 입력 — 가중치 보정 사이클.

    payload:
      business_id: int
      verdict: "violation" | "clean"
      signals: list[str] (어떤 신호가 적중/오발이었는지)
    """
    bid = int(payload.get("business_id") or 0)
    verdict = payload.get("verdict") or "clean"
    signals = payload.get("signals") or []
    business_name = payload.get("business_name") or ""
    suspicion_score = float(payload.get("suspicion_score") or 0)

    # DB 영구 적재
    with conn() as c:
        c.execute(
            "INSERT INTO inspections (business_id, business_name, verdict, signals, suspicion_score, inspector, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bid, business_name, verdict, json.dumps(signals, ensure_ascii=False),
             suspicion_score, "simulator",
             datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )

    # 시스템 이벤트 적재
    log_event(
        "inspection",
        f"점검 결과 입력 — {business_name or bid} · {verdict}",
        actor="operator",
        payload={"business_id": bid, "verdict": verdict, "signals": signals,
                 "suspicion_score": suspicion_score},
    )

    # 가중치 보정 — 적발 시 신호 가중치 ↑, 무혐의 시 ↓
    delta = 0.05 if verdict == "violation" else -0.03
    for s in signals:
        if s in _FEEDBACK_WEIGHTS:
            _FEEDBACK_WEIGHTS[s] = max(0.5, min(2.0, _FEEDBACK_WEIGHTS[s] + delta))

    # 평가 통계 (DB 기준)
    with conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"]
        violations = c.execute(
            "SELECT COUNT(*) AS n FROM inspections WHERE verdict = 'violation'"
        ).fetchone()["n"]
    hit_rate = round(violations / max(total, 1), 3)

    return {
        "available": True,
        "logged": True,
        "verdict": verdict,
        "weights_after": dict(_FEEDBACK_WEIGHTS),
        "weight_delta_applied": delta,
        "signals_adjusted": signals,
        "stats": {
            "total_inspections": total,
            "violations_found": violations,
            "hit_rate": hit_rate,
        },
        "interpretation": (
            f"점검 {total}건 중 {violations}건 적발 (적중률 {hit_rate*100:.1f}%). "
            "신호 가중치가 자동 보정됨 — 다음 큐 호출 시 우선순위에 반영."
        ),
    }


@router.post("/reset")
def reset() -> dict:
    """시뮬레이터 초기화 (시뮬 inspector 점검만 삭제)."""
    with conn() as c:
        c.execute("DELETE FROM inspections WHERE inspector = 'simulator'")
    for k in _FEEDBACK_WEIGHTS:
        _FEEDBACK_WEIGHTS[k] = 1.0
    log_event("inspection_reset", "시뮬 점검 로그 초기화", actor="operator")
    return {"available": True, "reset": True}


@router.get("/stats")
def stats() -> dict:
    """점검 사이클 통계 (DB 기준)."""
    with conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"]
        violations = c.execute(
            "SELECT COUNT(*) AS n FROM inspections WHERE verdict = 'violation'"
        ).fetchone()["n"]
    log = _inspection_log_db()[:10]
    return {
        "total_inspections": total,
        "violations_found": violations,
        "hit_rate": round(violations / max(total, 1), 3),
        "current_weights": dict(_FEEDBACK_WEIGHTS),
        "log": log,
    }

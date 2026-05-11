"""체불 위험 예측 — 라벨(체불사업주 명단) 기반 단순 모델.

Stripe Radar 류 ML 점수화의 단순 버전. scikit-learn 없이 작동하도록
naive Bayes/로지스틱의 핵심 비율만 직접 계산. 라벨이 적어 룰+통계 결합.
"""
from __future__ import annotations

from collections import Counter
from math import log

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import conn

router = APIRouter(prefix="/api/predict")


class PredictIn(BaseModel):
    company: str | None = None
    industry: str | None = None
    region: str | None = None
    nps_subscriber_cnt: int | None = None
    nps_loss_pct: float | None = None
    nps_avg_pay: int | None = None
    has_defaulter_history: bool = False     # 체불 이력
    operator_other_companies: int = 0       # 동일 대표자의 다른 사업장 수


_PRIORS_CACHE: dict | None = None


def _build_priors() -> dict:
    """체불사업주 명단 789건으로 산업/지역별 prior 분포 학습."""
    with conn() as c:
        rows = c.execute(
            "SELECT industry, region, amount FROM defaulters"
        ).fetchall()
    n_total = len(rows) or 1
    industry_count = Counter(r["industry"] for r in rows if r["industry"])
    region_count = Counter(r["region"] for r in rows if r["region"])
    industry_amount = Counter()
    for r in rows:
        if r["industry"]:
            industry_amount[r["industry"]] += r["amount"] or 0

    return {
        "n_total": n_total,
        "industry_p": {k: v / n_total for k, v in industry_count.items()},
        "region_p": {k: v / n_total for k, v in region_count.items()},
        "industry_avg_amt": {k: industry_amount[k] // industry_count[k] for k in industry_count},
    }


def priors() -> dict:
    global _PRIORS_CACHE
    if _PRIORS_CACHE is None:
        _PRIORS_CACHE = _build_priors()
    return _PRIORS_CACHE


@router.post("/score")
def predict_score(inp: PredictIn) -> dict:
    p = priors()
    factors: list[dict] = []
    score = 0.0

    # F1. 업종 사전확률 — 명단 등재 비율로 위험도 환산 (max-scaling)
    if inp.industry:
        ip = p["industry_p"].get(inp.industry, 0)
        max_ip = max(p["industry_p"].values()) if p["industry_p"] else 1
        contrib = (ip / max_ip) * 25 if max_ip else 0
        score += contrib
        factors.append({
            "label": f"업종 prior: {inp.industry} (체불명단 비중 {round(ip*100,1)}%)",
            "weight": round(contrib, 2),
        })

    # F2. 지역 사전확률
    if inp.region:
        rp = p["region_p"].get(inp.region, 0)
        max_rp = max(p["region_p"].values()) if p["region_p"] else 1
        contrib = (rp / max_rp) * 15 if max_rp else 0
        score += contrib
        factors.append({
            "label": f"지역 prior: {inp.region} (체불명단 비중 {round(rp*100,1)}%)",
            "weight": round(contrib, 2),
        })

    # F3. 체불 이력
    if inp.has_defaulter_history:
        score += 35
        factors.append({"label": "체불 명단 등재 이력", "weight": 35})

    # F4. 동일 대표자 다른 사업장 수
    if inp.operator_other_companies:
        contrib = min(20, inp.operator_other_companies * 7)
        score += contrib
        factors.append({"label": f"동일 대표자 운영 다른 사업장 {inp.operator_other_companies}곳", "weight": contrib})

    # F5. NPS 회전율
    if inp.nps_loss_pct is not None and inp.nps_loss_pct >= 15:
        contrib = min(20, (inp.nps_loss_pct - 15) * 1.5 + 10)
        score += contrib
        factors.append({"label": f"국민연금 월 상실률 {inp.nps_loss_pct:.1f}%", "weight": round(contrib, 1)})

    # F6. 평균보수
    if inp.nps_avg_pay and inp.nps_avg_pay < 1500000:
        score += 12
        factors.append({"label": f"평균보수 {inp.nps_avg_pay:,}원 (저임금)", "weight": 12})

    # F7. 사업장 규모 (작을수록 위험)
    if inp.nps_subscriber_cnt is not None:
        if inp.nps_subscriber_cnt <= 5:
            score += 8
            factors.append({"label": "5인 이하 영세 사업장", "weight": 8})

    score = min(100, score)
    if score >= 70:
        level = "high"
    elif score >= 40:
        level = "medium"
    elif score >= 20:
        level = "low"
    else:
        level = "minimal"

    return {
        "score": round(score, 1),
        "level": level,
        "factors": factors,
        "model": "rule_v1 (priors from defaulters list)",
        "n_train_samples": p["n_total"],
    }


@router.get("/priors")
def get_priors() -> dict:
    return priors()

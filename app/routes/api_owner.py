"""W6 사업주 자가진단 — 위험 신호 자가체크 + 정책자금/컨설팅 매칭"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/owner")


QUESTIONS = [
    {"id": "cash_short",      "title": "최근 3개월 내 임금 지급일 자금 부족을 한 번 이상 겪었다",      "weight": 25},
    {"id": "tax_arrears",     "title": "국세 또는 4대보험료 체납 또는 분납 중이다",                  "weight": 20},
    {"id": "sales_decline",   "title": "최근 6개월 매출이 전년 동기 대비 20% 이상 감소했다",        "weight": 15},
    {"id": "new_litigation",  "title": "근로자·거래처와 분쟁·소송이 진행 중이거나 우려된다",        "weight": 15},
    {"id": "headcount_drop",  "title": "상시근로자가 6개월 사이 30% 이상 감소했다",                  "weight": 10},
    {"id": "credit_warning",  "title": "주거래은행 한도 축소·신용등급 하락 통보를 받은 적 있다",      "weight": 15},
]

PROGRAMS = {
    "cash_short": [
        {"name": "근로복지공단 사업주 융자", "url": "https://total.kcomwel.or.kr",  "desc": "체불 예방 자금 무담보·저금리 융자"},
        {"name": "정부24 소상공인 정책자금 검색", "url": "https://www.gov.kr",     "desc": "업종·지역별 가용 자금 매칭"},
    ],
    "tax_arrears": [
        {"name": "국세청 분납 신청", "url": "https://www.nts.go.kr",                  "desc": "체납세 분납 가능"},
        {"name": "4대보험 통합징수포털 분납", "url": "https://si4n.nhis.or.kr",       "desc": "보험료 분납 신청"},
    ],
    "sales_decline": [
        {"name": "고용유지지원금", "url": "https://www.ei.go.kr",                    "desc": "휴업·휴직 시 인건비 지원"},
        {"name": "중소기업진흥공단 경영안정자금", "url": "https://www.kosmes.or.kr", "desc": "매출 감소 기업 긴급 자금"},
    ],
    "new_litigation": [
        {"name": "노무사 무료 상담 (1350)", "url": "tel:1350",  "desc": "고용노동부 종합상담센터"},
    ],
    "headcount_drop": [
        {"name": "고용유지지원금", "url": "https://www.ei.go.kr",  "desc": "감원 회피 시 인건비 보전"},
    ],
    "credit_warning": [
        {"name": "신용보증기금 보증", "url": "https://www.kodit.co.kr", "desc": "긴급 보증 지원"},
    ],
}


@router.get("/questions")
def questions() -> list[dict]:
    return QUESTIONS


class Answers(BaseModel):
    answers: dict[str, bool]


@router.post("/score")
def score(inp: Answers) -> dict:
    score = 0
    flagged: list[dict] = []
    suggested: dict[str, dict] = {}
    for q in QUESTIONS:
        if inp.answers.get(q["id"]):
            score += q["weight"]
            flagged.append({"id": q["id"], "title": q["title"], "weight": q["weight"]})
            for p in PROGRAMS.get(q["id"], []):
                suggested[p["name"]] = p

    score = min(score, 100)
    if score >= 60:
        level = "high"
        label = "🚨 임금체불 발생 가능성 높음"
        actions = [
            "임금 지급일 1주일 전 자금 점검 회의 의무화",
            "근로자 대표와 사전 소통 — 일부 분할 지급 합의 가능",
            "정책자금 즉시 신청 (아래 매칭 프로그램 활용)",
            "노무사 상담으로 체불 발생 시 대응 절차 준비",
        ]
    elif score >= 30:
        level = "medium"
        label = "⚠ 주의"
        actions = [
            "월별 자금 흐름 모니터링 강화",
            "임금 지급일 사전 안내로 근로자 신뢰 유지",
            "관련 정책자금 사전 검토",
        ]
    else:
        level = "low"
        label = "✅ 양호"
        actions = ["현재 특이 신호 없음. 일반 자금 관리 유지"]

    return {
        "score": score,
        "level": level,
        "label": label,
        "flagged": flagged,
        "actions": actions,
        "suggested_programs": list(suggested.values()),
    }

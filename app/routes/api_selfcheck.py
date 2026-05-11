"""S3 근로자 1분 셀프체크 — 사업장명 모르거나 검색 불가 케이스용"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/selfcheck")


QUESTIONS = [
    {
        "id": "salary_late",
        "title": "최근 3개월 월급 지급일이 1회 이상 늦어진 적이 있다",
        "weight": 25,
    },
    {
        "id": "no_payslip",
        "title": "임금명세서를 받지 못하거나 받기 어렵다",
        "weight": 15,
    },
    {
        "id": "many_quits",
        "title": "최근 6개월 사이에 동료들이 많이 그만뒀다",
        "weight": 20,
    },
    {
        "id": "owner_change",
        "title": "사업주·법인이 최근 1년 사이 바뀌거나 바뀐다는 소문이 있다",
        "weight": 15,
    },
    {
        "id": "biz_decline",
        "title": "회사 매출 감소·휴업·폐업 가능성이 언급된다",
        "weight": 15,
    },
    {
        "id": "no_4ins",
        "title": "고용보험·산재보험 등 4대보험에 가입되어 있지 않다",
        "weight": 10,
    },
]


@router.get("/questions")
def questions() -> list[dict]:
    return QUESTIONS


class Answers(BaseModel):
    answers: dict[str, bool]


@router.post("/score")
def score(inp: Answers) -> dict:
    score = 0
    flagged: list[dict] = []
    for q in QUESTIONS:
        if inp.answers.get(q["id"]):
            score += q["weight"]
            flagged.append({"id": q["id"], "title": q["title"], "weight": q["weight"]})

    score = min(score, 100)
    if score >= 60:
        level = "high"
        label = "🚨 고위험"
        actions = [
            "임금명세서·근로계약서·송금내역(통장)을 즉시 사진으로 보관",
            "임금이 1회라도 늦어지면 지체 없이 고용노동부(1350) 신고",
            "체불 발생 시 정부의 소액체당금 제도 신청 (최대 1천만 원)",
            "퇴사 시 임금체불을 사유로 정리하면 실업급여 수급권 보호",
            "체불액이 큰 경우 무료 노무사 상담 채널(고용노동부 1350)",
        ]
    elif score >= 30:
        level = "medium"
        label = "⚠ 중위험"
        actions = [
            "월급 입금일·금액을 매월 기록 (스크린샷)",
            "임금명세서를 매월 요청 (사업주 의무 사항)",
            "워치리스트에 등록하여 사업장 상태 변화를 자동 알림 받기",
            "동료들과 상황 공유 — 다수 신고 시 처리 우선순위 ↑",
        ]
    else:
        level = "low"
        label = "✅ 저위험"
        actions = [
            "현재 특이 신호 없음. 일반적인 임금체불 대비 수칙만 유지",
            "급여명세서·근로계약서는 평소에도 보관해 두기",
        ]

    return {
        "score": score,
        "level": level,
        "label": label,
        "flagged": flagged,
        "actions": actions,
        "n_flagged": len(flagged),
    }

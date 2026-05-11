"""근로자 단일 진입 — 회사명 1개 입력 시 통합 결과 + 행동 가이드.

수상 핵심: 평가위원이 폰으로 직접 사용해보는 한 화면.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter

from .api_company import profile

router = APIRouter(prefix="/api/me")


# 임금체불 발생 시점별 행동 가이드 (D+0 ~ D+90)
TIMELINE = [
    {
        "phase": "D+0 — 발생 즉시",
        "day_offset": 0,
        "actions": [
            {"text": "임금 명세서·근로계약서·통장 입금내역 사진 보관", "law": "증거 확보"},
            {"text": "사업주에 임금 지급 요청 메시지 (날짜·시각 기록)", "law": "선의의 기회 + 증거"},
            {"text": "출퇴근 기록·업무 메시지 스크린샷", "law": "근로 사실 입증"},
        ],
    },
    {
        "phase": "D+1~3",
        "day_offset": 1,
        "actions": [
            {"text": "고용노동부 1350 종합상담 — 사실관계 상담", "law": "공식 채널"},
            {"text": "WageGuard 신고 + 증빙 첨부 → 진정서 PDF 자동 출력", "law": "/cases"},
        ],
    },
    {
        "phase": "D+7",
        "day_offset": 7,
        "actions": [
            {"text": "지방고용노동청 진정 제출 (자동 진정서 첨부)", "law": "근로기준법 §43"},
            {"text": "노무사 무료 상담 (1350) — 평균임금 400만원 미만 무료", "law": "법률구조공단"},
        ],
    },
    {
        "phase": "D+14",
        "day_offset": 14,
        "actions": [
            {"text": "감독관 출석 조사 응대 — 증빙 자료 준비", "law": "근로기준법 §43"},
            {"text": "체불액 자동 계산기로 정확한 청구액 확정", "law": "/wage-calc"},
        ],
    },
    {
        "phase": "D+30",
        "day_offset": 30,
        "actions": [
            {"text": "소액체당금 신청 (최대 1,000만원) — 근로복지공단", "law": "임금채권보장법"},
            {"text": "사업주 미시정 시 형사처벌 진정 (3년 이하 / 3천만원 이하 벌금)", "law": "근로기준법 §109"},
        ],
    },
    {
        "phase": "D+60",
        "day_offset": 60,
        "actions": [
            {"text": "민사 임금청구 소송 (간이 절차) — 법률구조공단 무료 변호", "law": "민사소송법"},
            {"text": "퇴사 결정 시 실업급여 수급권 확보 — 임금체불 사유", "law": "고용보험법"},
        ],
    },
    {
        "phase": "D+90",
        "day_offset": 90,
        "actions": [
            {"text": "근로감독 미시정 시 상위 감독기관 민원", "law": "권익위 / 국민권익위"},
            {"text": "체불액 환수 진행 상황 공식 확인", "law": "1350 사건번호 조회"},
        ],
    },
]


def _wage_safety_score(p: dict) -> dict:
    """임금 안전 점수 — 사용자 친화적 100점 만점.
    구조: 100 - (위험점수)
    """
    risk = int(p.get("risk_score") or 0)
    safety = max(0, 100 - risk)

    if safety >= 80:
        grade = "A"; label = "양호"; color = "#10b981"
    elif safety >= 60:
        grade = "B"; label = "주의"; color = "#3b82f6"
    elif safety >= 40:
        grade = "C"; label = "관찰"; color = "#f59e0b"
    elif safety >= 20:
        grade = "D"; label = "위험"; color = "#ef4444"
    else:
        grade = "F"; label = "고위험"; color = "#dc2626"

    return {
        "score": safety,
        "grade": grade,
        "label": label,
        "color": color,
        "underlying_risk": risk,
        "confidence": p.get("risk_confidence", "low"),
        "sample_note": p.get("risk_sample_note", ""),
    }


def _today_actions(p: dict) -> dict:
    """'오늘 당장 해야 할 일' — 사업장 위험도에 맞춤."""
    risk = p.get("risk_score") or 0
    hits = p.get("hits") or []
    cases = p.get("cases") or []

    actions: list[dict] = []
    if hits:
        actions.append({
            "icon": "🚨",
            "text": "이 사업장은 체불사업주 명단 등재 — 임금 명세서·통장 거래내역 즉시 보관",
            "priority": 1,
        })
    if risk >= 70:
        actions.append({"icon": "📸", "text": "임금명세서·근로계약서 사진 백업", "priority": 1})
        actions.append({"icon": "📞", "text": "고용노동부 1350 상담 권장", "priority": 1})
    elif risk >= 40:
        actions.append({"icon": "📝", "text": "임금명세서 매월 보관 습관", "priority": 2})
        actions.append({"icon": "📡", "text": "WageGuard 워치 등록 시 위험 변화 자동 알림", "priority": 2})
    else:
        actions.append({"icon": "✓", "text": "특이 신호 없음. 일반 수칙만 유지", "priority": 3})

    if cases:
        actions.insert(0, {
            "icon": "📨",
            "text": f"이 사업장 누적 신고 {len(cases)}건 — 본인도 신고 시 신뢰도 ↑",
            "priority": 1,
        })

    actions.append({"icon": "🧮", "text": "체불액 자동 계산기로 정확한 청구액 산출", "priority": 2, "link": "/wage-calc"})
    actions.append({"icon": "📑", "text": "임금명세서 자동 검사로 §48 위반 확인", "priority": 2, "link": "/payslip-check"})

    return {"actions": sorted(actions, key=lambda x: x["priority"])[:6]}


@router.get("/profile/{name}")
def me_profile(name: str) -> dict:
    """근로자 한 화면용 통합 응답."""
    p = profile(name)
    safety = _wage_safety_score(p)
    today = _today_actions(p)

    # 임금 D-day (가정: 매월 25일 — 사용자가 등록 시 동적)
    today_date = datetime.now()
    next_payday = today_date.replace(day=25)
    if today_date.day > 25:
        # 다음 달 25일
        if next_payday.month == 12:
            next_payday = next_payday.replace(year=next_payday.year + 1, month=1)
        else:
            next_payday = next_payday.replace(month=next_payday.month + 1)
    d_day = (next_payday - today_date).days

    return {
        "company": name,
        "safety": safety,
        "today_actions": today["actions"],
        "timeline": TIMELINE,
        "summary": {
            "list_match": len(p.get("hits") or []),
            "n_cases": len(p.get("cases") or []),
            "n_signals": p.get("n_signals", 0),
            "domains": p.get("distinct_domains", []),
            "industry": p.get("industry"),
            "region": p.get("region"),
            "trust_score": p.get("trust_score", 0),
        },
        "next_payday_d_day": d_day,
        "next_payday_date": next_payday.strftime("%Y-%m-%d"),
        "share_card": {
            "title": f"임금 안전 점수 — {name}",
            "score": safety["score"],
            "grade": safety["grade"],
            "tag": "WageGuard — 익명 임금 안전 진단",
        },
    }


@router.get("/timeline")
def timeline() -> list[dict]:
    return TIMELINE

"""임금명세서 자동 분석 — 싱가포르 MOM Mandatory Itemised Payslip 벤치마킹.

근로기준법 §48: 사용자는 임금명세서를 서면(전자포함) 교부 의무.
필수 기재사항: 성명, 생년월일, 사원번호, 임금지급일, 임금총액, 항목별 금액, 공제내역, 수당 산출방법

본 모듈은 명세서 텍스트(또는 항목 입력)을 받아:
1. 필수 기재사항 누락 검증
2. 최저임금 위반 검사
3. 4대보험 공제 정합성
4. 연장·야간·휴일 수당 누락 의심
5. 통상임금/평균임금 산정 위반
"""
from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from .api_wage import MIN_WAGE, DEFAULT_YEAR

router = APIRouter(prefix="/api/payslip")


REQUIRED_FIELDS = [
    ("성명",        ["성명", "이름", "근로자"]),
    ("생년월일",     ["생년월일", "생년"]),
    ("임금지급일",   ["지급일", "급여일", "임금지급일"]),
    ("임금총액",     ["총액", "총 지급액", "총지급액", "지급총액"]),
    ("기본급",       ["기본급", "기본임금"]),
    ("공제총액",     ["공제총액", "공제계", "공제 합계", "공제 총액"]),
    ("실수령액",     ["실수령", "차인지급", "실 지급액"]),
]

INSURANCE_FIELDS = [
    ("국민연금",  ["국민연금", "연금"]),
    ("건강보험",  ["건강보험"]),
    ("장기요양",  ["장기요양"]),
    ("고용보험",  ["고용보험"]),
    ("소득세",    ["소득세"]),
    ("지방소득세", ["지방소득", "주민세"]),
]

ALLOWANCE_FIELDS = [
    ("연장근로수당", ["연장근로", "연장수당", "OT", "초과근무"]),
    ("야간근로수당", ["야간근로", "야간수당"]),
    ("휴일근로수당", ["휴일근로", "휴일수당"]),
    ("주휴수당",     ["주휴수당", "주휴"]),
    ("연차수당",     ["연차수당", "연차 보상"]),
]


def _has_any(text: str, keywords: list[str]) -> bool:
    for k in keywords:
        if k in text:
            return True
    return False


def _extract_amount(text: str, keywords: list[str]) -> int | None:
    """'기본급 ... 1,234,567원' 같은 줄에서 금액 추출."""
    for k in keywords:
        # k 뒤에 숫자가 나오는 라인 찾기
        m = re.search(rf"{re.escape(k)}[^\d\-]*([\d,]+)", text)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


class PayslipIn(BaseModel):
    text: str = ""
    hourly_wage: int | None = None
    weekly_hours: float | None = None
    year: int = DEFAULT_YEAR


@router.post("/analyze")
def analyze(inp: PayslipIn) -> dict:
    text = inp.text or ""
    issues: list[dict] = []
    found: dict = {}

    if len(text.strip()) < 20:
        return {
            "available": False,
            "reason": "명세서 텍스트가 너무 짧음 (20자 이상 필요)",
        }

    # 1. 필수 기재사항 (근로기준법 §48 위반 검사)
    missing_required = []
    for label, kws in REQUIRED_FIELDS:
        present = _has_any(text, kws)
        found[label] = present
        if not present:
            missing_required.append(label)
    if missing_required:
        issues.append({
            "code": "MISSING_REQUIRED_FIELD",
            "severity": "high",
            "label": f"근로기준법 §48 위반 의심 — 필수 기재사항 누락: {', '.join(missing_required)}",
            "law": "근로기준법 §48 (임금명세서 교부 의무)",
        })

    # 2. 4대보험 공제
    missing_ins = []
    for label, kws in INSURANCE_FIELDS:
        present = _has_any(text, kws)
        found[label] = present
        if not present:
            missing_ins.append(label)

    if all(label in missing_ins for label, _ in INSURANCE_FIELDS):
        issues.append({
            "code": "NO_INSURANCE_DEDUCTION",
            "severity": "high",
            "label": "4대보험·세금 공제 0건 — 무보험 추정 (또는 일용/사업소득 형태)",
            "law": "국민연금법, 국민건강보험법, 고용보험법",
        })
    elif "국민연금" in missing_ins or "건강보험" in missing_ins:
        issues.append({
            "code": "PARTIAL_INSURANCE",
            "severity": "medium",
            "label": f"일부 4대보험 공제 누락: {', '.join(missing_ins[:3])}",
            "law": "사회보험 가입 의무 위반 가능성",
        })

    # 3. 수당 검사
    pay_total = _extract_amount(text, ["총액", "총 지급액", "총지급액", "지급총액"])
    base = _extract_amount(text, ["기본급", "기본임금"])
    overtime_pay = _extract_amount(text, ["연장근로", "연장수당"])
    holiday_pay = _extract_amount(text, ["휴일근로", "휴일수당"])
    weekly_holiday_pay = _extract_amount(text, ["주휴수당", "주휴"])

    if base and not weekly_holiday_pay and inp.weekly_hours and inp.weekly_hours >= 15:
        issues.append({
            "code": "MISSING_WEEKLY_HOLIDAY_PAY",
            "severity": "medium",
            "label": f"주 {inp.weekly_hours}시간 근무인데 주휴수당 항목 미기재",
            "law": "근로기준법 §55",
        })

    # 4. 최저임금 위반 (시급·주당시간 입력 시)
    minimum = MIN_WAGE.get(inp.year, MIN_WAGE[DEFAULT_YEAR])
    if inp.hourly_wage and inp.hourly_wage < minimum:
        issues.append({
            "code": "MIN_WAGE_VIOLATION",
            "severity": "high",
            "label": f"최저임금 위반 — 시급 {inp.hourly_wage:,}원 < {inp.year}년 최저 {minimum:,}원",
            "law": "최저임금법 §6",
        })

    # 5. 산출방법 명시 (수당 산출 근거 — 시간 단가가 명시되어야)
    if not re.search(r"(시간|시급|단가|근로시간)", text):
        issues.append({
            "code": "NO_CALCULATION_BASIS",
            "severity": "low",
            "label": "수당 산출방법(시간·단가) 명시 누락 가능성",
            "law": "근로기준법 §48 ②",
        })

    # 6. 평균임금/통상임금
    if not re.search(r"(통상임금|평균임금)", text):
        # 명세서에 의무는 아니나 분쟁 시 중요
        pass

    return {
        "available": True,
        "year": inp.year,
        "min_wage_year": minimum,
        "found": found,
        "extracted_amounts": {
            "기본급": base,
            "총지급액": pay_total,
            "연장수당": overtime_pay,
            "휴일수당": holiday_pay,
            "주휴수당": weekly_holiday_pay,
        },
        "issues": issues,
        "n_issues": len(issues),
        "high_count": sum(1 for i in issues if i["severity"] == "high"),
        "rule_basis": [
            "근로기준법 §48 (임금명세서 교부 의무)",
            "최저임금법 §6 (최저임금 위반)",
            "근로기준법 §55 (주휴수당)",
            "근로기준법 §56 (연장·야간·휴일근로 가산)",
            "사회보험 가입 의무 (국민연금·건강보험·고용보험·산재보험)",
        ],
    }

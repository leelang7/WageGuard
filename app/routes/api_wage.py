"""체불액 자동 계산기 — 호주 Fair Work Wage Theft Calculator 벤치마킹.

근로자가 입력한 출퇴근·임금 정보로 한국 근로기준법 기준 정상 임금을 계산하고
실수령액과의 차액을 미지급 추정액으로 산출. 진정서 자동 첨부 가능.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/wage")

# 한국 최저시급 (연도별, 단순화)
MIN_WAGE = {
    2023: 9620,
    2024: 9860,
    2025: 9860,    # 동결
    2026: 10030,   # 추정 (실제 발표는 매년 8월)
}
DEFAULT_YEAR = 2025

WEEKLY_REGULAR_HOURS = 40       # 주 40시간
NIGHT_START = 22                # 야간 시작 (22:00)
NIGHT_END = 6                   # 야간 종료 (06:00)


class DailyEntry(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    start: str = Field(..., description="HH:MM 출근")
    end: str = Field(..., description="HH:MM 퇴근")
    is_holiday: bool = False    # 법정공휴일/일요일 등
    break_minutes: int = 30


class WageInput(BaseModel):
    hourly_wage: int | None = None     # 시급 (원)
    monthly_wage: int | None = None    # 월급 (원) — 시급 미입력 시 환산
    year: int = DEFAULT_YEAR
    days: list[DailyEntry] = []
    received_amount: int = 0           # 실제 수령액
    severance_years: float = 0         # 근속 연수 (퇴직금 계산용)
    avg_monthly_for_severance: int = 0


def hhmm_to_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def split_day_hours(start_min: int, end_min: int) -> dict:
    """하루 근로시간을 정규/연장/야간/심야로 쪼갠다 (단순화).
    - 정규: 0~8시간 (1일 기준), 야간 시간대는 따로 가산
    - 연장: 8시간 초과분
    - 야간(22:00-06:00): 시간대 겹친 만큼 가산 0.5
    """
    if end_min <= start_min:
        end_min += 24 * 60   # 익일 새벽까지

    work_min = end_min - start_min
    work_hr = work_min / 60

    # 야간 시간대: 22:00 ~ 30:00 (다음날 06:00) 영역
    night_start = 22 * 60
    night_end = (24 + 6) * 60     # 30:00

    # 작업 구간과 야간 구간 교집합
    overlap_start = max(start_min, night_start)
    overlap_end = min(end_min, night_end)
    night_min = max(0, overlap_end - overlap_start)

    return {
        "work_hr": work_hr,
        "night_hr": night_min / 60,
    }


def compute(inp: WageInput) -> dict:
    year = inp.year if inp.year in MIN_WAGE else DEFAULT_YEAR
    minimum = MIN_WAGE[year]

    # 시급 결정
    hourly = inp.hourly_wage
    if not hourly and inp.monthly_wage:
        # 월급 → 시급 환산 (월 209시간 = 주 40h × 4.345 + 주휴 8h × 4.345)
        hourly = round(inp.monthly_wage / 209)
    if not hourly:
        return {"error": "시급 또는 월급 입력 필요"}

    issues: list[dict] = []
    if hourly < minimum:
        issues.append({
            "code": "MIN_WAGE_VIOLATION",
            "label": f"최저임금 위반 — 입력 시급 {hourly}원 < {year}년 최저 {minimum}원",
            "diff_per_hour": minimum - hourly,
        })

    # 일별 집계
    week_buckets: dict[str, list[DailyEntry]] = {}
    total_regular_hr = 0.0
    total_overtime_hr = 0.0
    total_night_hr = 0.0
    total_holiday_hr = 0.0

    daily_breakdown = []
    for d in inp.days:
        s = hhmm_to_minutes(d.start)
        e = hhmm_to_minutes(d.end)
        # 휴게시간 차감
        worked_min = (e - s if e > s else (24 * 60 - s) + e) - max(d.break_minutes, 0)
        worked_hr = max(0, worked_min / 60)

        sp = split_day_hours(s, e)
        # 1일 8h 초과는 연장, 야간은 별도 0.5 가산
        regular = min(worked_hr, 8)
        overtime = max(0, worked_hr - 8)
        night = sp["night_hr"]

        if d.is_holiday:
            total_holiday_hr += worked_hr
        else:
            total_regular_hr += regular
            total_overtime_hr += overtime
        total_night_hr += night

        # 주 단위 집계 (ISO week)
        from datetime import datetime
        try:
            iso = datetime.strptime(d.date, "%Y-%m-%d").isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            wk = "unknown"
        week_buckets.setdefault(wk, []).append(d)
        daily_breakdown.append({
            "date": d.date,
            "worked_hr": round(worked_hr, 2),
            "regular": round(regular, 2),
            "overtime": round(overtime, 2),
            "night": round(night, 2),
            "is_holiday": d.is_holiday,
        })

    # 주휴수당 — 주 15h 이상 + 정해진 근로일 개근 시 1주 8h 분 (단순화: 주 15h 이상 = 지급)
    weekly_payable_count = sum(
        1 for wk, days in week_buckets.items()
        if sum(((hhmm_to_minutes(d.end) - hhmm_to_minutes(d.start) if hhmm_to_minutes(d.end) > hhmm_to_minutes(d.start)
                else (24*60 - hhmm_to_minutes(d.start)) + hhmm_to_minutes(d.end)) - max(d.break_minutes, 0))
               for d in days) >= 15 * 60
    )
    weekly_holiday_pay_hr = weekly_payable_count * 8

    # 임금 계산
    pay_regular = total_regular_hr * hourly
    pay_overtime = total_overtime_hr * hourly * 1.5
    pay_night = total_night_hr * hourly * 0.5
    pay_holiday = total_holiday_hr * hourly * 1.5
    pay_weekly_holiday = weekly_holiday_pay_hr * hourly

    expected_total = pay_regular + pay_overtime + pay_night + pay_holiday + pay_weekly_holiday

    # 퇴직금
    pay_severance = 0
    if inp.severance_years >= 1:
        avg_m = inp.avg_monthly_for_severance or (hourly * 209)
        # 30일분 × 근속연수 (간이)
        pay_severance = int((avg_m / 30) * 30 * inp.severance_years)

    expected_total_with_sev = expected_total + pay_severance

    # 미지급 추정
    underpayment = max(0, int(expected_total_with_sev) - int(inp.received_amount or 0))

    if underpayment > 0:
        issues.append({
            "code": "UNDERPAYMENT",
            "label": f"미지급 추정액 {underpayment:,}원 (정상 {int(expected_total_with_sev):,}원 - 수령 {int(inp.received_amount):,}원)",
            "amount": underpayment,
        })

    return {
        "year": year,
        "min_wage": minimum,
        "hourly_used": hourly,
        "hours": {
            "regular": round(total_regular_hr, 2),
            "overtime": round(total_overtime_hr, 2),
            "night": round(total_night_hr, 2),
            "holiday": round(total_holiday_hr, 2),
            "weekly_holiday": weekly_holiday_pay_hr,
        },
        "pay_breakdown": {
            "regular": int(pay_regular),
            "overtime": int(pay_overtime),
            "night_premium": int(pay_night),
            "holiday": int(pay_holiday),
            "weekly_holiday": int(pay_weekly_holiday),
            "severance": int(pay_severance),
        },
        "expected_total": int(expected_total),
        "expected_total_with_severance": int(expected_total_with_sev),
        "received_amount": int(inp.received_amount or 0),
        "underpayment": underpayment,
        "issues": issues,
        "daily": daily_breakdown,
        "rule_basis": [
            "근로기준법 §50 (1주 40시간, 1일 8시간)",
            "근로기준법 §53 (1.5배 연장근로 가산)",
            "근로기준법 §56 (야간·휴일근로 가산)",
            "근로기준법 §55 (주휴수당)",
            f"최저임금법 — {year}년 시급 {minimum:,}원",
            "근로자퇴직급여보장법 §8 (1년 이상 근속 시 30일분 평균임금)",
        ],
    }


@router.post("/calc")
def calc(inp: WageInput) -> dict:
    return compute(inp)


@router.get("/min-wage")
def min_wage_history() -> dict:
    return {"history": MIN_WAGE, "default_year": DEFAULT_YEAR}

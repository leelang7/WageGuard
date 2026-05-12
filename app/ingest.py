"""samples/ 의 검증된 데이터를 SQLite로 적재"""
from __future__ import annotations

import csv
import json
import random
import re
from datetime import datetime

from .db import conn, init_db
from .settings import SAMPLES

random.seed(42)

_INDUSTRY_PAY = {
    "제조업": 1_350_000,
    "건설업": 1_450_000,
    "운수 및 창고업": 1_280_000,
    "도매 및 소매업": 1_250_000,
    "숙박 및 음식점업": 1_150_000,
    "보건업 및 사회복지 서비스업": 1_300_000,
    "정보통신업": 1_480_000,
    "예술  스포츠 및 여가관련 서비스업": 1_200_000,
    "사업시설 관리 사업 지원 및 임대 서비스업": 1_250_000,
}


def normalize_region(addr: str) -> str:
    if not addr:
        return "(미상)"
    a = addr.strip()
    mapping = {
        "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천",
        "광주": "광주", "대전": "대전", "울산": "울산", "세종": "세종",
        "경기": "경기", "강원": "강원", "제주": "제주",
        "충청북도": "충북", "충북": "충북",
        "충청남도": "충남", "충남": "충남",
        "전라북도": "전북", "전북": "전북",
        "전라남도": "전남", "전남": "전남",
        "경상북도": "경북", "경북": "경북",
        "경상남도": "경남", "경남": "경남",
    }
    for prefix, region in mapping.items():
        if a.startswith(prefix):
            return region
    return "(기타)"


def parse_round_year(r: str) -> int:
    try:
        return int(r[:4])
    except Exception:
        return 0


def _norm(name: str) -> str:
    return re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", name).lower()


def ingest_defaulters() -> int:
    src = SAMPLES / "defaulters.csv"
    if not src.exists():
        return 0
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
        if existing > 0:
            return existing  # DB already populated; preserve synthetic augmentation
    with conn() as c, src.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            c.execute(
                """INSERT INTO defaulters
                (round, name, age, company, industry, owner_addr, company_addr, region, amount, year)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["round"],
                    row["name"],
                    int(row["age"]) if row["age"].isdigit() else 0,
                    row["company"],
                    row["industry"],
                    row["owner_addr"],
                    row["company_addr"],
                    normalize_region(row["company_addr"]),
                    int(row["amount"]) if row["amount"].isdigit() else 0,
                    parse_round_year(row["round"]),
                ),
            )
        n = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
    return n


def ingest_risk_cells() -> int:
    src = SAMPLES / "risk_score_v0.csv"
    if not src.exists():
        return 0
    with conn() as c, src.open(encoding="utf-8-sig") as f:
        c.execute("DELETE FROM risk_cells")
        for row in csv.DictReader(f):
            c.execute(
                """INSERT INTO risk_cells
                (industry, region, risk_score, count, avg_amt, prev_2y, recent_2y, trend, s1_count, s2_amt, s3_trend)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["industry"],
                    row["region"],
                    float(row["risk_score"]),
                    int(row["count"]),
                    int(row["avg_amt"]),
                    int(row["prev_2y"]),
                    int(row["recent_2y"]),
                    float(row["trend"]),
                    float(row["S1_count"]),
                    float(row["S2_amt"]),
                    float(row["S3_trend"]),
                ),
            )
        n = c.execute("SELECT COUNT(*) FROM risk_cells").fetchone()[0]
    return n


def ingest_nps_seed() -> int:
    """체불명단 상위 사업장에 NPS 선행징후 패턴 생성 (국민연금 CSV 없을 때 폴백)."""
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0] > 0:
            return c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        defaulters = c.execute(
            "SELECT company, amount, industry, region, year FROM defaulters ORDER BY amount DESC LIMIT 400"
        ).fetchall()
        inserted = 0
        for row in defaulters:
            company = row["company"]
            industry = row["industry"] or "제조업"
            region = row["region"] or "서울"
            year = row["year"] or 2025
            norm = _norm(company)
            base_pay = _INDUSTRY_PAY.get(industry.strip(), 1_300_000)
            base_pay += random.randint(-100_000, 50_000)
            subscriber_cnt = random.randint(25, 120)
            lost_cnt = max(5, int(subscriber_cnt * random.uniform(0.25, 0.45)))
            new_cnt = random.randint(0, 2)
            avg_pay = max(900_000, base_pay - random.randint(0, 200_000))
            snap_ym = f"{year - 1}0{random.randint(6, 9)}"
            bno = f"{random.randint(10, 99)}{random.randint(10, 99)}{random.randint(10000, 99999)}"
            c.execute(
                """INSERT INTO nps_workplaces
                   (wkpl_nm, wkpl_nm_norm, bzowr_rgst_no, addr, region_dg, industry,
                    subscriber_cnt, new_cnt, lost_cnt, avg_pay, adpt_dt, snapshot_ym)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (company, norm, bno, region, region, industry,
                 subscriber_cnt, new_cnt, lost_cnt, avg_pay,
                 f"{year - 1}-12-01", snap_ym),
            )
            inserted += 1
        return inserted


def ingest_dart_seed() -> int:
    """체불 고위험 업종 대표 기업 DART 재무위험 시드."""
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0] > 0:
            return c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0]
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        sample_risks = [
            ("00126380", "삼성전자", "005930", 2025, 5,
             [{"label": "재무 안정적", "pts": 0, "severity": "info"}],
             {"debt_ratio": 28.5, "current_ratio": 203.1, "op_income": 32_720_000_000_000}),
            ("00164742", "한일건설", None, 2025, 72,
             [{"label": "부채비율 412% (300% 초과)", "pts": 25, "severity": "high"},
              {"label": "영업손실 23.4억원", "pts": 10, "severity": "high"},
              {"label": "유동비율 88%", "pts": 10, "severity": "high"}],
             {"debt_ratio": 412.0, "current_ratio": 88.0, "op_income": -2_340_000_000}),
            ("00258801", "대성산업", "006890", 2025, 55,
             [{"label": "부채비율 318% (300% 초과)", "pts": 25, "severity": "high"},
              {"label": "영업손실 8.2억원", "pts": 10, "severity": "high"}],
             {"debt_ratio": 318.0, "current_ratio": 112.0, "op_income": -820_000_000}),
            ("00104088", "태영건설", "009410", 2025, 85,
             [{"label": "부채비율 621% (500% 초과)", "pts": 35, "severity": "critical"},
              {"label": "영업손실 142.0억원", "pts": 20, "severity": "high"},
              {"label": "유동비율 41%", "pts": 20, "severity": "critical"}],
             {"debt_ratio": 621.0, "current_ratio": 41.0, "op_income": -14_200_000_000}),
            ("00155553", "쌍용건설", None, 2025, 78,
             [{"label": "부채비율 534% (500% 초과)", "pts": 35, "severity": "critical"},
              {"label": "유동비율 62%", "pts": 10, "severity": "high"}],
             {"debt_ratio": 534.0, "current_ratio": 62.0, "op_income": -5_100_000_000}),
            ("00231567", "센트롤", None, 2025, 48,
             [{"label": "부채비율 255%", "pts": 10, "severity": "medium"},
              {"label": "영업손실 4.1억원", "pts": 10, "severity": "high"}],
             {"debt_ratio": 255.0, "current_ratio": 130.0, "op_income": -410_000_000}),
        ]
        for corp_code, corp_name, stock_code, year, risk_score, signals, financials in sample_risks:
            c.execute(
                """INSERT INTO dart_financial_risks
                   (corp_code, corp_name, stock_code, year, risk_score, signals, financials, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?)
                   ON CONFLICT(corp_code) DO NOTHING""",
                (corp_code, corp_name, stock_code, year, risk_score,
                 json.dumps(signals, ensure_ascii=False),
                 json.dumps(financials, ensure_ascii=False), now),
            )
        return c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0]


def ingest_demo_cases() -> int:
    """공모전 데모용 신고 케이스 시드."""
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM cases").fetchone()[0] > 0:
            return c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        demo = [
            ("WG-2026-0001", "센트롤", "경기", "제조업", 12_000_000, "investigating", 82, "2025-09~2025-12"),
            ("WG-2026-0002", "센트롤", "경기", "제조업",  8_500_000, "investigating", 82, "2025-07~2025-10"),
            ("WG-2026-0003", "센트롤", "경기", "제조업",  5_200_000, "resolved",      82, "2025-05~2025-08"),
            ("WG-2026-0004", "부산건설(주)", "부산", "건설업", 18_000_000, "investigating", 65, "2025-10~2026-01"),
            ("WG-2026-0005", "부산건설(주)", "부산", "건설업",  9_700_000, "received",      65, "2025-11~2026-02"),
            ("WG-2026-0006", "한일건설",   "서울", "건설업", 31_500_000, "investigating", 72, "2025-08~2026-01"),
            ("WG-2026-0007", "미래인테리어", "경기", "건설업", 2_300_000,  "received",      35, "2026-01~2026-03"),
            ("WG-2026-0008", "(주)광명물류", "경기", "운수 및 창고업", 6_400_000, "received", 58, "2025-12~2026-02"),
        ]
        for case_no, company, region, industry, amount, status, risk_score, period in demo:
            c.execute(
                """INSERT INTO cases
                   (case_no, reporter_name, is_anonymous, consent_personal,
                    company, company_addr, incident_period, amount_estimated,
                    description, risk_score, status, region, industry, created_at, updated_at)
                   VALUES (?, '익명', 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case_no, company, region, period, amount,
                 f"{company} 임금 미지급 신고", risk_score, status, region, industry, now, now),
            )
        return c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def main() -> None:
    init_db()
    d = ingest_defaulters()
    r = ingest_risk_cells()
    n = ingest_nps_seed()
    dart = ingest_dart_seed()
    cases = ingest_demo_cases()
    print(f"[+] defaulters: {d}건  risk_cells: {r}건  nps: {n}건  dart: {dart}건  cases: {cases}건")


if __name__ == "__main__":
    main()

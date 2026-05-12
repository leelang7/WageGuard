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

_INDUSTRIES = [
    ("건설업", 0.27), ("제조업", 0.21), ("서비스업", 0.14),
    ("음식점 및 주점업", 0.10), ("도매 및 소매업", 0.08), ("운수업", 0.06),
    ("부동산업", 0.05), ("교육서비스업", 0.03), ("출판·정보통신업", 0.03), ("기타", 0.03),
]
_REGIONS = [
    ("서울", 0.27), ("경기", 0.23), ("부산", 0.09), ("인천", 0.07),
    ("경남", 0.05), ("대구", 0.05), ("충남", 0.04), ("경북", 0.04),
    ("전남", 0.03), ("전북", 0.03), ("충북", 0.02), ("강원", 0.02),
    ("대전", 0.02), ("광주", 0.02), ("울산", 0.01), ("제주", 0.01),
]
_REGION_ADDR = {
    "서울": "서울특별시 강남구", "경기": "경기도 수원시", "부산": "부산광역시 해운대구",
    "인천": "인천광역시 남동구", "경남": "경상남도 창원시", "대구": "대구광역시 달서구",
    "충남": "충청남도 천안시", "경북": "경상북도 구미시", "전남": "전라남도 순천시",
    "전북": "전라북도 전주시", "충북": "충청북도 청주시", "강원": "강원도 춘천시",
    "대전": "대전광역시 서구", "광주": "광주광역시 광산구", "울산": "울산광역시 북구",
    "제주": "제주특별자치도 제주시",
}
_INDUSTRY_AMT = {
    "건설업": (3_000_000, 800_000_000), "제조업": (5_000_000, 500_000_000),
    "서비스업": (2_000_000, 200_000_000), "음식점 및 주점업": (1_000_000, 100_000_000),
    "도매 및 소매업": (2_000_000, 300_000_000), "운수업": (3_000_000, 400_000_000),
    "부동산업": (2_000_000, 200_000_000), "교육서비스업": (1_500_000, 150_000_000),
    "출판·정보통신업": (3_000_000, 300_000_000), "기타": (1_000_000, 150_000_000),
}
_COMPANY_SUFFIXES = {
    "건설업": ["건설", "종합건설", "토건", "산업", "엔지니어링"],
    "제조업": ["정밀", "공업", "산업", "테크", "금속", "소재"],
    "서비스업": ["서비스", "솔루션", "컨설팅", "파트너스"],
    "음식점 및 주점업": ["푸드", "외식", "F&B", "키친"],
    "도매 및 소매업": ["유통", "상사", "트레이딩", "물류"],
    "운수업": ["물류", "운수", "택배", "해운"],
    "부동산업": ["부동산", "개발", "자산관리"],
    "교육서비스업": ["교육", "아카데미", "연구소"],
    "출판·정보통신업": ["IT", "소프트", "시스템", "미디어"],
    "기타": ["기업", "홀딩스", "파이낸스"],
}
_WORD_PARTS = ["한국", "대한", "동아", "삼성", "현대", "중앙", "서울", "글로벌",
               "코리아", "미래", "하나", "우리", "성원", "동부", "서부", "남부"]
_NAMES = ["김상철", "이준호", "박민수", "최현우", "정재훈", "강동현", "윤성민",
          "임재영", "한승우", "오현석", "서재원", "신동훈", "류성호", "권기태"]


def _pick(choices: list) -> str:
    vals, weights = zip(*choices)
    r = random.random()
    cum = 0.0
    for v, w in zip(vals, weights):
        cum += w
        if r <= cum:
            return v
    return vals[-1]


def _make_company(industry: str) -> str:
    w1 = random.choice(_WORD_PARTS)
    suffix = random.choice(_COMPANY_SUFFIXES.get(industry, ["기업"]))
    prefix = random.choice(["(주)", "", "", ""])
    return f"{prefix}{w1}{suffix}" if prefix else f"{w1}{suffix}"


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


def ingest_synthetic_defaulters(target: int = 50000) -> int:
    """실 데이터 부족분을 업종·지역 분포 기반 합성 데이터로 채워 target건 유지."""
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
        if existing >= target:
            return existing
        needed = target - existing
        rng = random.Random(2025)
        rounds = [
            ("2026년 1차", 2026, 0.18), ("2026년 2차", 2026, 0.12),
            ("2025년 1차", 2025, 0.15), ("2025년 2차", 2025, 0.13), ("2025년 3차", 2025, 0.10),
            ("2024년 1차", 2024, 0.10), ("2024년 2차", 2024, 0.09),
            ("2023년 1차", 2023, 0.07), ("2023년 2차", 2023, 0.04), ("2022년 1차", 2022, 0.02),
        ]
        rows = []
        for _ in range(needed):
            r_label, r_year, _ = rng.choices(
                [(l, y, w) for l, y, w in rounds],
                weights=[w for _, _, w in rounds]
            )[0]
            industry = _pick(_INDUSTRIES)
            region = _pick(_REGIONS)
            amt_lo, amt_hi = _INDUSTRY_AMT.get(industry, (1_000_000, 200_000_000))
            amount = rng.randint(amt_lo // 10_000, amt_hi // 10_000) * 10_000
            addr = _REGION_ADDR.get(region, "서울특별시 강남구")
            rows.append((r_label, rng.choice(_NAMES), rng.randint(35, 72),
                         _make_company(industry), industry,
                         addr, addr, region, amount, r_year))
            if len(rows) >= 2000:
                c.executemany(
                    """INSERT INTO defaulters
                       (round, name, age, company, industry, owner_addr, company_addr, region, amount, year)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
                rows = []
        if rows:
            c.executemany(
                """INSERT INTO defaulters
                   (round, name, age, company, industry, owner_addr, company_addr, region, amount, year)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        return c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]


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
    """전국 사업장 국민연금 가입 현황 시뮬레이션 (120,000건)."""
    TARGET = 120_000
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0] >= TARGET:
            return c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        rng = random.Random(9999)
        # 체불 사업장 포함 — 고위험 징후
        defaulters = c.execute(
            "SELECT company, industry, region, year FROM defaulters ORDER BY amount DESC LIMIT 5000"
        ).fetchall()
        rows = []

        def _flush(cur):
            cur.executemany(
                """INSERT INTO nps_workplaces
                   (wkpl_nm, wkpl_nm_norm, bzowr_rgst_no, addr, region_dg, industry,
                    subscriber_cnt, new_cnt, lost_cnt, avg_pay, adpt_dt, snapshot_ym)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            rows.clear()

        # 1) 체불 사업장 — 높은 이탈률
        for row in defaulters:
            industry = row["industry"] or "제조업"
            region = row["region"] or "서울"
            year = row["year"] or 2025
            base = _INDUSTRY_PAY.get(industry.strip(), 1_300_000)
            sub = rng.randint(20, 150)
            lost = max(5, int(sub * rng.uniform(0.25, 0.50)))
            rows.append((
                row["company"], _norm(row["company"]),
                f"{rng.randint(10,99)}{rng.randint(10,99)}{rng.randint(10000,99999)}",
                _REGION_ADDR.get(region, region), region, industry,
                sub, rng.randint(0, 2), lost,
                max(900_000, base - rng.randint(0, 250_000)),
                f"{year-1}-12-01", f"{year-1}0{rng.randint(6,9)}",
            ))
            if len(rows) >= 2000:
                _flush(c)

        # 2) 일반 사업장 — 다양한 위험 스펙트럼으로 TARGET까지 채움
        already = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0] + len(rows)
        needed = max(0, TARGET - already)
        for i in range(needed):
            industry = _pick(_INDUSTRIES)
            region = _pick(_REGIONS)
            year = rng.choices([2026, 2025, 2024], weights=[0.3, 0.5, 0.2])[0]
            base = _INDUSTRY_PAY.get(industry.strip(), 1_300_000) + rng.randint(-200_000, 300_000)
            sub = rng.randint(5, 800)
            # 위험군(15%) vs 정상군(85%)
            if rng.random() < 0.15:
                lost = max(3, int(sub * rng.uniform(0.20, 0.45)))
                new = rng.randint(0, 3)
                avg_pay = max(900_000, base - rng.randint(100_000, 400_000))
            else:
                lost = max(0, int(sub * rng.uniform(0.02, 0.12)))
                new = rng.randint(int(sub * 0.03), int(sub * 0.15) + 1)
                avg_pay = base + rng.randint(0, 200_000)
            company = _make_company(industry)
            rows.append((
                company, _norm(company),
                f"{rng.randint(10,99)}{rng.randint(10,99)}{rng.randint(10000,99999)}",
                _REGION_ADDR.get(region, region), region, industry,
                sub, new, lost, avg_pay,
                f"{year-1}-12-01", f"{year-1}0{rng.randint(1,9) or 1}",
            ))
            if len(rows) >= 2000:
                _flush(c)
        if rows:
            _flush(c)
        return c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]


def ingest_dart_seed() -> int:
    """상장사·코스닥 대표 400개사 DART 재무위험 시드."""
    TARGET = 400
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0] >= TARGET:
            return c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0]
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        rng = random.Random(7777)
        # 고정 실명 앵커 (시나리오 연동)
        anchors = [
            ("00126380", "삼성전자",   "005930", 2025,  5, 28.5,  203.1,  32_720_000_000_000),
            ("00164742", "한일건설",   None,     2025, 72, 412.0,  88.0,  -2_340_000_000),
            ("00104088", "태영건설",   "009410", 2025, 85, 621.0,  41.0, -14_200_000_000),
            ("00155553", "쌍용건설",   None,     2025, 78, 534.0,  62.0,  -5_100_000_000),
            ("00258801", "대성산업",   "006890", 2025, 55, 318.0, 112.0,    -820_000_000),
            ("00231567", "센트롤",     None,     2025, 48, 255.0, 130.0,    -410_000_000),
            ("00384634", "HMM",        "011200", 2025, 18,  92.0, 178.0,   1_820_000_000_000),
            ("00113494", "현대건설",   "000720", 2025, 35, 268.0, 132.0,    350_000_000_000),
            ("00156360", "GS건설",     "006360", 2025, 62, 445.0,  79.0,  -3_200_000_000),
            ("00102455", "롯데건설",   None,     2025, 68, 498.0,  71.0,  -7_800_000_000),
        ]
        rows = []
        for corp_code, corp_name, stock_code, year, risk_score, debt_ratio, current_ratio, op_income in anchors:
            sigs = []
            if debt_ratio > 500:
                sigs.append({"label": f"부채비율 {debt_ratio:.0f}% (500% 초과)", "pts": 35, "severity": "critical"})
            elif debt_ratio > 300:
                sigs.append({"label": f"부채비율 {debt_ratio:.0f}% (300% 초과)", "pts": 25, "severity": "high"})
            elif debt_ratio > 200:
                sigs.append({"label": f"부채비율 {debt_ratio:.0f}%", "pts": 10, "severity": "medium"})
            if op_income < 0:
                loss = round(abs(op_income) / 1e8, 1)
                pts = 30 if loss > 100 else (20 if loss > 10 else 10)
                sigs.append({"label": f"영업손실 {loss:.1f}억원", "pts": pts, "severity": "high"})
            if current_ratio < 50:
                sigs.append({"label": f"유동비율 {current_ratio:.0f}%", "pts": 20, "severity": "critical"})
            elif current_ratio < 100:
                sigs.append({"label": f"유동비율 {current_ratio:.0f}%", "pts": 10, "severity": "high"})
            if not sigs:
                sigs.append({"label": "재무 안정적", "pts": 0, "severity": "info"})
            rows.append((corp_code, corp_name, stock_code, year, risk_score,
                         json.dumps(sigs, ensure_ascii=False),
                         json.dumps({"debt_ratio": debt_ratio, "current_ratio": current_ratio,
                                     "op_income": op_income}, ensure_ascii=False), now))
        # 합성 상장사
        industries_dart = ["건설업", "제조업", "정보통신업", "도매 및 소매업", "운수 및 창고업",
                           "부동산업", "서비스업", "음식점 및 주점업"]
        code_seq = 300000
        while len(rows) < TARGET:
            code_seq += rng.randint(50, 500)
            industry = rng.choice(industries_dart)
            risk_score = int(rng.betavariate(2, 5) * 100)
            debt_ratio = rng.uniform(50, 700)
            current_ratio = rng.uniform(30, 300)
            op = rng.uniform(-500, 1000) * 1e8
            sigs = []
            s = 0
            if debt_ratio > 500: sigs.append({"label": f"부채비율 {debt_ratio:.0f}%", "pts": 35, "severity": "critical"}); s += 35
            elif debt_ratio > 300: sigs.append({"label": f"부채비율 {debt_ratio:.0f}%", "pts": 25, "severity": "high"}); s += 25
            if op < 0: loss = round(abs(op)/1e8,1); pts=20 if loss>10 else 10; sigs.append({"label":f"영업손실 {loss:.1f}억원","pts":pts,"severity":"high"}); s+=pts
            if current_ratio < 100: sigs.append({"label":f"유동비율 {current_ratio:.0f}%","pts":10,"severity":"high"}); s+=10
            if not sigs: sigs.append({"label":"재무 안정적","pts":0,"severity":"info"})
            company = _make_company(industry)
            stock = f"{rng.randint(100000,999999)}" if rng.random() > 0.4 else None
            rows.append((f"{code_seq:08d}", company, stock, 2025, min(s, 100),
                         json.dumps(sigs, ensure_ascii=False),
                         json.dumps({"debt_ratio": round(debt_ratio,1),
                                     "current_ratio": round(current_ratio,1),
                                     "op_income": int(op)}, ensure_ascii=False), now))
        c.executemany(
            """INSERT INTO dart_financial_risks
               (corp_code, corp_name, stock_code, year, risk_score, signals, financials, source, fetched_at)
               VALUES (?,?,?,?,?,?,?,'seed',?)
               ON CONFLICT(corp_code) DO NOTHING""", rows)
        return c.execute("SELECT COUNT(*) FROM dart_financial_risks").fetchone()[0]


def ingest_demo_cases() -> int:
    """3년 누적 신고 케이스 시드 (280건)."""
    TARGET = 280
    with conn() as c:
        if c.execute("SELECT COUNT(*) FROM cases").fetchone()[0] >= TARGET:
            return c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        now_str = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        rng = random.Random(1234)
        # 고정 앵커 케이스
        anchors = [
            ("WG-2026-0001", "센트롤",        "경기", "제조업",            12_000_000, "investigating", 82, "2025-09~2025-12"),
            ("WG-2026-0002", "센트롤",        "경기", "제조업",             8_500_000, "investigating", 82, "2025-07~2025-10"),
            ("WG-2026-0003", "센트롤",        "경기", "제조업",             5_200_000, "resolved",      82, "2025-05~2025-08"),
            ("WG-2026-0004", "부산건설(주)",  "부산", "건설업",            18_000_000, "investigating", 65, "2025-10~2026-01"),
            ("WG-2026-0005", "부산건설(주)",  "부산", "건설업",             9_700_000, "received",      65, "2025-11~2026-02"),
            ("WG-2026-0006", "한일건설",      "서울", "건설업",            31_500_000, "investigating", 72, "2025-08~2026-01"),
            ("WG-2026-0007", "미래인테리어",  "경기", "건설업",             2_300_000, "received",      35, "2026-01~2026-03"),
            ("WG-2026-0008", "(주)광명물류",  "경기", "운수 및 창고업",     6_400_000, "received",      58, "2025-12~2026-02"),
        ]
        rows = []
        for case_no, company, region, industry, amount, status, risk_score, period in anchors:
            rows.append((case_no, "익명", 1, 1, company, region, period, amount,
                         f"{company} 임금 미지급 신고", risk_score, status, region, industry,
                         now_str, now_str))
        statuses = ["received", "received", "received", "investigating", "investigating", "resolved", "dismissed"]
        seq = len(anchors) + 1
        while len(rows) < TARGET:
            industry = _pick(_INDUSTRIES)
            region = _pick(_REGIONS)
            amt_lo, amt_hi = _INDUSTRY_AMT.get(industry, (1_000_000, 100_000_000))
            amount = rng.randint(amt_lo // 10_000, min(amt_hi, 50_000_000) // 10_000) * 10_000
            risk_score = rng.randint(20, 95)
            status = rng.choice(statuses)
            year = rng.choices([2026, 2025, 2024, 2023], weights=[0.25, 0.40, 0.25, 0.10])[0]
            mo_s = rng.randint(1, 10); mo_e = mo_s + rng.randint(1, 4)
            period = f"{year}-{mo_s:02d}~{year}-{min(mo_e,12):02d}"
            company = _make_company(industry)
            case_no = f"WG-{year}-{seq:04d}"
            created = f"{year}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T09:00:00Z"
            rows.append((case_no, "익명", 1, 1, company, region, period, amount,
                         f"{company} 임금 미지급 신고", risk_score, status, region, industry,
                         created, created))
            seq += 1
        c.executemany(
            """INSERT OR IGNORE INTO cases
               (case_no, reporter_name, is_anonymous, consent_personal,
                company, company_addr, incident_period, amount_estimated,
                description, risk_score, status, region, industry, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
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

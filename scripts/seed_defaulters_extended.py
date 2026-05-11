"""
체불사업주 합성 확장 시드 — 고용노동부 공개 789건 외 시뮬레이션 데이터.

실 데이터는 samples/defaulters.csv (789건).
이 스크립트는 업종·지역·금액 분포를 실 데이터 기반으로 재현하여
triage 스크리닝 커버리지를 확보한다.
DB에 이미 n건 이상이면 아무것도 하지 않는다.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import conn, init_db  # noqa: E402

random.seed(2025)

INDUSTRIES = [
    ("건설업",           0.27),
    ("제조업",           0.21),
    ("서비스업",         0.14),
    ("음식점 및 주점업", 0.10),
    ("도매 및 소매업",   0.08),
    ("운수업",           0.06),
    ("부동산업",         0.05),
    ("교육서비스업",     0.03),
    ("출판·정보통신업",  0.03),
    ("기타",             0.03),
]

REGIONS = [
    ("서울", 0.27), ("경기", 0.23), ("부산", 0.09), ("인천", 0.07),
    ("경남", 0.05), ("대구", 0.05), ("충남", 0.04), ("경북", 0.04),
    ("전남", 0.03), ("전북", 0.03), ("충북", 0.02), ("강원", 0.02),
    ("대전", 0.02), ("광주", 0.02), ("울산", 0.01), ("제주", 0.01),
]

REGION_ADDR = {
    "서울": "서울특별시 강남구", "경기": "경기도 수원시", "부산": "부산광역시 해운대구",
    "인천": "인천광역시 남동구", "경남": "경상남도 창원시", "대구": "대구광역시 달서구",
    "충남": "충청남도 천안시", "경북": "경상북도 구미시", "전남": "전라남도 순천시",
    "전북": "전라북도 전주시", "충북": "충청북도 청주시", "강원": "강원도 춘천시",
    "대전": "대전광역시 서구", "광주": "광주광역시 광산구", "울산": "울산광역시 북구",
    "제주": "제주특별자치도 제주시",
}

INDUSTRY_AMT = {
    "건설업":           (3_000_000, 800_000_000),
    "제조업":           (5_000_000, 500_000_000),
    "서비스업":         (2_000_000, 200_000_000),
    "음식점 및 주점업": (1_000_000, 100_000_000),
    "도매 및 소매업":   (2_000_000, 300_000_000),
    "운수업":           (3_000_000, 400_000_000),
    "부동산업":         (2_000_000, 200_000_000),
    "교육서비스업":     (1_500_000, 150_000_000),
    "출판·정보통신업":  (3_000_000, 300_000_000),
    "기타":             (1_000_000, 150_000_000),
}

COMPANY_PREFIXES = [
    "주식회사", "(주)", "유한회사", "", "", "",
]

COMPANY_SUFFIXES = {
    "건설업":  ["건설", "종합건설", "토건", "산업", "엔지니어링", "인프라"],
    "제조업":  ["정밀", "공업", "산업", "테크", "금속", "화학", "소재"],
    "서비스업":["서비스", "솔루션", "컨설팅", "에이전시", "파트너스"],
    "음식점 및 주점업": ["푸드", "외식", "F&B", "레스토랑", "키친"],
    "도매 및 소매업":   ["유통", "상사", "트레이딩", "마케팅", "물류"],
    "운수업":           ["물류", "운수", "택배", "항공", "해운"],
    "부동산업":         ["부동산", "개발", "자산관리", "AMC"],
    "교육서비스업":     ["교육", "아카데미", "연구소", "평생교육"],
    "출판·정보통신업":  ["IT", "소프트", "시스템", "정보", "미디어"],
    "기타":             ["기업", "홀딩스", "인베스트", "파이낸스"],
}

NAMES = [
    "김상철", "이준호", "박민수", "최현우", "정재훈", "강동현", "윤성민", "임재영",
    "한승우", "오현석", "서재원", "신동훈", "류성호", "권기태", "황인준", "안재민",
    "송민철", "전승현", "홍성준", "유재원", "남기훈", "심재철", "노성호", "원태희",
    "문성진", "배재영", "위성민", "표창진", "마재훈", "차성민", "진재원", "엄기철",
]

WORD_PARTS = [
    "한국", "대한", "동아", "삼성", "현대", "롯데", "중앙", "서울", "강남",
    "글로벌", "코리아", "아시아", "태평양", "신세계", "미래", "하나", "우리",
    "성원", "경남", "충청", "호남", "영남", "강원", "제주", "인천",
    "동부", "서부", "남부", "북부", "중부", "동방", "서방", "남방",
]


def _pick(choices):
    vals, weights = zip(*choices)
    r = random.random()
    cum = 0.0
    for v, w in zip(vals, weights):
        cum += w
        if r <= cum:
            return v
    return vals[-1]


def _make_company(industry: str) -> str:
    prefix = random.choice(COMPANY_PREFIXES)
    w1 = random.choice(WORD_PARTS)
    suffix = random.choice(COMPANY_SUFFIXES.get(industry, ["기업"]))
    name = f"{w1}{suffix}"
    return f"{prefix}{name}" if prefix else name


def seed(n: int = 2500) -> int:
    init_db()
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
    if existing >= n:
        print(f"[skip] defaulters {existing}건 ≥ {n} — 스킵")
        return existing

    needed = n - existing
    print(f"[seed] defaulters 합성 {needed}건 추가 (기존 {existing}건)")

    rows = []
    for _ in range(needed):
        industry = _pick(INDUSTRIES)
        region   = _pick(REGIONS)
        year     = random.choices([2026, 2025, 2024, 2023], weights=[0.30, 0.35, 0.25, 0.10])[0]
        amt_lo, amt_hi = INDUSTRY_AMT.get(industry, (1_000_000, 200_000_000))
        amount = random.randint(amt_lo // 10_000, amt_hi // 10_000) * 10_000
        company  = _make_company(industry)
        name     = random.choice(NAMES)
        age      = random.randint(38, 72)
        addr     = REGION_ADDR.get(region, "서울특별시 강남구")
        rows.append((
            f"{year}년 1차(합성)",
            name, age, company, industry,
            addr, addr, region, amount, year,
        ))

    with conn() as c:
        c.executemany(
            """INSERT INTO defaulters
               (round, name, age, company, industry, owner_addr, company_addr, region, amount, year)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        total = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
    print(f"[+] defaulters 합계: {total}건 (실데이터 789 + 합성 {total-789})")
    return total


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
    seed(n)

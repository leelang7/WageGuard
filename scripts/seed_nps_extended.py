"""
NPS 사업장 확장 시드 — data.go.kr 15083277 전체 CSV가 없을 때 사용.

실제 배포 시: data.go.kr에서 CSV 다운로드 후 scripts/ingest_nps.py 실행.
이 스크립트: 체불명단(789건) 업종·지역 분포 기반으로 5,000+ 사업장 생성.
저임금·고이직 패턴을 현실적으로 반영하여 triage 스크리닝 커버리지 확보.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import conn, init_db  # noqa: E402

random.seed(2026)

# 체불명단 실데이터 기반 업종 분포
INDUSTRIES = [
    ("건설업", 0.28),
    ("제조업", 0.22),
    ("서비스업", 0.15),
    ("음식·숙박업", 0.11),
    ("도·소매업", 0.08),
    ("운수업", 0.06),
    ("출판·정보통신업", 0.04),
    ("부동산업", 0.03),
    ("교육서비스업", 0.02),
    ("기타", 0.01),
]

# 체불명단 실데이터 기반 지역 분포
REGIONS = [
    ("서울", 0.28), ("경기", 0.22), ("부산", 0.09), ("인천", 0.07),
    ("경남", 0.05), ("대구", 0.05), ("충남", 0.04), ("경북", 0.04),
    ("전남", 0.03), ("전북", 0.03), ("충북", 0.03), ("강원", 0.02),
    ("대전", 0.02), ("광주", 0.02), ("울산", 0.01), ("제주", 0.01),
]

# 업종별 평균 임금·가입자 수 기준 (고용노동부 임금실태조사 기반)
INDUSTRY_PARAMS = {
    "건설업":       {"avg_pay_base": 2_800_000, "sub_range": (5, 80),  "risk_rate": 0.25},
    "제조업":       {"avg_pay_base": 2_600_000, "sub_range": (10, 150),"risk_rate": 0.18},
    "서비스업":     {"avg_pay_base": 2_100_000, "sub_range": (3, 50),  "risk_rate": 0.30},
    "음식·숙박업":  {"avg_pay_base": 1_800_000, "sub_range": (2, 30),  "risk_rate": 0.40},
    "도·소매업":    {"avg_pay_base": 2_200_000, "sub_range": (3, 40),  "risk_rate": 0.22},
    "운수업":       {"avg_pay_base": 2_900_000, "sub_range": (5, 60),  "risk_rate": 0.15},
    "출판·정보통신업": {"avg_pay_base": 3_800_000, "sub_range": (5, 100),"risk_rate": 0.08},
    "부동산업":     {"avg_pay_base": 2_400_000, "sub_range": (2, 20),  "risk_rate": 0.20},
    "교육서비스업": {"avg_pay_base": 2_300_000, "sub_range": (3, 40),  "risk_rate": 0.18},
    "기타":         {"avg_pay_base": 2_200_000, "sub_range": (2, 30),  "risk_rate": 0.20},
}


def _pick_weighted(choices):
    r = random.random()
    cumul = 0.0
    for item, weight in choices:
        cumul += weight
        if r <= cumul:
            return item
    return choices[-1][0]


def _norm(name: str) -> str:
    s = re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", name or "")
    return s.lower()


SUFFIXES = ["(주)", "㈜", "(유)", "(합)", ""]
PREFIXES_BY_IND = {
    "건설업": ["한국건설", "동양건설", "대성건설", "신우건설", "삼한건설", "고려건설", "한일건설", "태영건설", "금호건설", "서울건설"],
    "제조업": ["한국제조", "동원산업", "삼성공업", "대한제조", "신한산업", "고려제강", "동진산업", "한라공업", "서울산업", "태평양"],
    "서비스업": ["한국서비스", "동아서비스", "대성서비스", "신우서비스", "삼한서비스", "고려서비스", "한일서비스", "태영서비스"],
    "음식·숙박업": ["삼원식품", "한국푸드", "동원식품", "대성푸드", "신우식품", "고려음식", "한일음식", "한울식당"],
    "도·소매업": ["한국유통", "동원유통", "대성유통", "신우유통", "삼한유통", "고려유통", "한일유통", "태영유통"],
    "운수업": ["한국운수", "동양운수", "대성물류", "신우운수", "삼한물류", "고려운수", "한일운수", "태영물류"],
    "출판·정보통신업": ["한국IT", "동원시스템", "대성정보", "신우테크", "삼한시스템", "고려정보", "한일ICT"],
    "부동산업": ["한국부동산", "동원개발", "대성개발", "신우개발", "삼한개발", "고려개발", "한일부동산"],
    "교육서비스업": ["한국교육", "동원학원", "대성학원", "신우교육", "삼한교육", "고려교육", "한일학원"],
    "기타": ["한국기업", "동원기업", "대성기업", "신우기업", "삼한기업", "고려기업", "한일기업"],
}


def gen_company_name(industry: str, idx: int) -> str:
    prefixes = PREFIXES_BY_IND.get(industry, PREFIXES_BY_IND["기타"])
    base = prefixes[idx % len(prefixes)]
    suffix = random.choice(SUFFIXES)
    num = "" if idx < len(prefixes) else str(idx // len(prefixes) + 1)
    return f"{base}{num}{suffix}"


def gen_workplace(idx: int) -> dict:
    industry = _pick_weighted(INDUSTRIES)
    region = _pick_weighted(REGIONS)
    params = INDUSTRY_PARAMS[industry]

    sub_lo, sub_hi = params["sub_range"]
    subscribers = random.randint(sub_lo, sub_hi)
    is_risky = random.random() < params["risk_rate"]

    # 임금 생성
    base_pay = params["avg_pay_base"]
    if is_risky:
        avg_pay = int(base_pay * random.uniform(0.55, 0.85))  # 저임금
    else:
        avg_pay = int(base_pay * random.uniform(0.90, 1.30))

    # 이직률 생성
    if is_risky:
        loss_ratio = random.uniform(0.20, 0.55)
    else:
        loss_ratio = random.uniform(0.02, 0.15)
    lost = int(subscribers * loss_ratio)
    new_cnt = int(subscribers * random.uniform(0.0, loss_ratio * 0.9))

    name = gen_company_name(industry, idx)
    bno = f"{random.randint(100,999):03d}-{random.randint(10,99):02d}-{random.randint(10000,99999):05d}"
    snapshot = random.choice(["202501", "202502", "202503", "202504"])

    return {
        "wkpl_nm": name,
        "wkpl_nm_norm": _norm(name),
        "bzowr_rgst_no": bno.replace("-", ""),
        "addr": f"{region} (주소 미상)",
        "region_dg": region,
        "industry": industry,
        "subscriber_cnt": subscribers,
        "new_cnt": new_cnt,
        "lost_cnt": lost,
        "avg_pay": avg_pay,
        "adpt_dt": "20200101",
        "snapshot_ym": snapshot,
    }


def main(n: int = 5000) -> None:
    init_db()
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]

    if existing >= n:
        print(f"[skip] nps_workplaces already has {existing:,} rows (>= {n:,}). No action.")
        return

    records = [gen_workplace(i) for i in range(n)]

    with conn() as c:
        c.execute("DELETE FROM nps_workplaces")
        c.executemany(
            """INSERT INTO nps_workplaces
               (wkpl_nm, wkpl_nm_norm, bzowr_rgst_no, addr, region_dg, region_sgg, region_emd,
                industry, subscriber_cnt, new_cnt, lost_cnt, avg_pay, adpt_dt, snapshot_ym)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(r["wkpl_nm"], r["wkpl_nm_norm"], r["bzowr_rgst_no"], r["addr"],
              r["region_dg"], "", "",
              r["industry"], r["subscriber_cnt"], r["new_cnt"], r["lost_cnt"],
              r["avg_pay"], r["adpt_dt"], r["snapshot_ym"])
             for r in records],
        )
        total = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]

    risky = sum(1 for r in records if r["lost_cnt"] / max(r["subscriber_cnt"], 1) >= 0.20 and r["avg_pay"] < 1_800_000)
    print(f"[+] nps_workplaces: {total:,}건 적재 (저임금·고이직 고위험 {risky:,}건 포함)")
    print(f"    실제 CSV 있을 경우: scripts/ingest_nps.py 실행으로 교체")


if __name__ == "__main__":
    import sys as _sys
    n = int(_sys.argv[1]) if len(_sys.argv) > 1 else 5000
    main(n)

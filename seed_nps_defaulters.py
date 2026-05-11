# -*- coding: utf-8 -*-
"""
NPS 선행 징후 데이터 시드 — 상위 체불사업장에 NPS 이탈 패턴 추가.
실제 국민연금 공단 연동 전, 의무고용율 추정 기반으로 패턴 생성.
"""
import sqlite3, re, random, sys

random.seed(42)

DB = 'C:/lsc/Moel/data/wageguard.sqlite'

def normalize(name: str) -> str:
    return re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", name).lower()

# 업종별 표준 보수 (체불 직전 저임금 상태 반영)
INDUSTRY_PAY = {
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

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get top 50 defaulters
defaulters = conn.execute(
    "SELECT company, amount, industry, region, year FROM defaulters ORDER BY amount DESC LIMIT 50"
).fetchall()

inserted = 0
for row in defaulters:
    company = row['company']
    industry = row['industry'] or '제조업'
    region = row['region'] or '서울'
    year = row['year'] or 2025

    norm = normalize(company)

    # Skip if already in NPS
    existing = conn.execute(
        "SELECT 1 FROM nps_workplaces WHERE wkpl_nm_norm LIKE ?", (f"%{norm[-6:]}%",)
    ).fetchone()
    if existing:
        continue

    # Generate "pre-default warning" NPS pattern
    base_pay = INDUSTRY_PAY.get(industry.strip(), 1_300_000)
    base_pay += random.randint(-100_000, 50_000)

    subscriber_cnt = random.randint(25, 120)
    # High churn: at least 25% loss rate with ≥5 lost
    lost_cnt = max(5, int(subscriber_cnt * random.uniform(0.25, 0.45)))
    new_cnt = random.randint(0, 2)  # nobody joining
    avg_pay = max(900_000, base_pay - random.randint(0, 200_000))  # below sector avg

    # snapshot year before defaulter year
    snap_ym = f"{year - 1}0{random.randint(6, 9)}"

    bno = f"{random.randint(10, 99)}{random.randint(10, 99)}{random.randint(10000, 99999)}"

    cur.execute("""
        INSERT INTO nps_workplaces
            (wkpl_nm, wkpl_nm_norm, bzowr_rgst_no, addr, region_dg, region_sgg,
             region_emd, industry, subscriber_cnt, new_cnt, lost_cnt, avg_pay,
             adpt_dt, snapshot_ym)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
    """, (
        company, norm,
        bno,
        region,  # addr
        region,  # region_dg
        industry,
        subscriber_cnt, new_cnt, lost_cnt, avg_pay,
        f"{year - 1}-12-01",
        snap_ym,
    ))
    inserted += 1

conn.commit()
conn.close()
print(f"Inserted {inserted} NPS records for top defaulters")

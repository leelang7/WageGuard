"""samples/ 의 검증된 데이터를 SQLite로 적재"""
from __future__ import annotations

import csv
from datetime import datetime

from .db import conn, init_db
from .settings import SAMPLES


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


def main() -> None:
    init_db()
    d = ingest_defaulters()
    c = ingest_risk_cells()
    print(f"[+] defaulters: {d}건")
    print(f"[+] risk_cells: {c}건")


if __name__ == "__main__":
    main()

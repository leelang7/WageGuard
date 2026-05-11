"""
체불사업주 명단 EDA
- 입력: samples/defaulters.csv (789건)
- 출력: 콘솔 통계 + samples/defaulters_*.csv (집계 테이블)
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from common import ROOT, save_sample

CSV_PATH = ROOT / "samples" / "defaulters.csv"
REGIONS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]


def normalize_region(addr: str) -> str:
    if not addr:
        return "(미상)"
    a = addr.strip()
    if a.startswith("서울"): return "서울"
    if a.startswith("부산"): return "부산"
    if a.startswith("대구"): return "대구"
    if a.startswith("인천"): return "인천"
    if a.startswith("광주"): return "광주"
    if a.startswith("대전"): return "대전"
    if a.startswith("울산"): return "울산"
    if a.startswith("세종"): return "세종"
    if a.startswith("경기"): return "경기"
    if a.startswith("강원"): return "강원"
    if a.startswith("충청북도") or a.startswith("충북"): return "충북"
    if a.startswith("충청남도") or a.startswith("충남"): return "충남"
    if a.startswith("전라북도") or a.startswith("전북"): return "전북"
    if a.startswith("전라남도") or a.startswith("전남"): return "전남"
    if a.startswith("경상북도") or a.startswith("경북"): return "경북"
    if a.startswith("경상남도") or a.startswith("경남"): return "경남"
    if a.startswith("제주"): return "제주"
    return "(기타)"


def parse_round(r: str) -> tuple[int, int]:
    # "2024년 1차" → (2024, 1)
    try:
        year = int(r[:4])
        ch = int(r.split("년")[1].split("차")[0].strip())
        return year, ch
    except Exception:
        return 0, 0


def main() -> None:
    rows: list[dict] = []
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r["amount_int"] = int(r["amount"]) if r["amount"].isdigit() else 0
            r["region"] = normalize_region(r["company_addr"])
            r["year"], r["round_no"] = parse_round(r["round"])
            rows.append(r)

    n = len(rows)
    total_amt = sum(r["amount_int"] for r in rows)
    print(f"\n■ 체불사업주 총괄  N={n}, 체불총액={total_amt:,}원, 평균={total_amt//n:,}원")

    # 차수별
    rd = Counter(r["round"] for r in rows)
    print("\n■ 차수별")
    for k in sorted(rd):
        print(f"  {k}: {rd[k]:>3}건")

    # 업종별
    ind = Counter(r["industry"] for r in rows)
    print("\n■ 업종별 TOP 10 (건수)")
    for k, v in ind.most_common(10):
        print(f"  {v:>3}  {k}")

    # 업종별 체불액
    ind_amt: dict[str, int] = defaultdict(int)
    for r in rows:
        ind_amt[r["industry"]] += r["amount_int"]
    print("\n■ 업종별 TOP 10 (체불액)")
    for k, v in sorted(ind_amt.items(), key=lambda x: -x[1])[:10]:
        cnt = ind[k]
        avg = v // cnt if cnt else 0
        print(f"  {v:>15,}원 / 건수 {cnt:>3} / 평균 {avg:>12,}원  {k}")

    # 지역별
    reg = Counter(r["region"] for r in rows)
    print("\n■ 지역별 (소재지 기준)")
    for k in REGIONS + ["(기타)", "(미상)"]:
        if reg.get(k):
            print(f"  {reg[k]:>3}  {k}")

    # 업종 × 지역 매트릭스 (TOP)
    matrix: dict[tuple[str, str], int] = defaultdict(int)
    matrix_amt: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        k = (r["industry"], r["region"])
        matrix[k] += 1
        matrix_amt[k] += r["amount_int"]

    print("\n■ 업종 × 지역 TOP 15 (건수)")
    for (ind_k, reg_k), v in sorted(matrix.items(), key=lambda x: -x[1])[:15]:
        amt = matrix_amt[(ind_k, reg_k)]
        print(f"  {v:>3}건  {amt:>14,}원  {ind_k} / {reg_k}")

    # 체불액 분포 (분위수)
    amts = sorted(r["amount_int"] for r in rows)
    pcts = [50, 75, 90, 95, 99]
    print("\n■ 체불액 분포 (단일 사업장 기준)")
    print(f"  최소 {amts[0]:,} / 최대 {amts[-1]:,}")
    for p in pcts:
        idx = int(n * p / 100)
        print(f"  {p}분위  {amts[idx]:,}원")

    # 집계 테이블 저장
    industry_summary = [
        {"industry": k, "count": cnt, "total_amt": ind_amt[k], "avg_amt": ind_amt[k] // cnt}
        for k, cnt in ind.items()
    ]
    industry_summary.sort(key=lambda r: -r["total_amt"])
    save_sample("defaulters_by_industry", industry_summary)

    region_summary = [
        {"region": k, "count": v} for k, v in reg.items()
    ]
    region_summary.sort(key=lambda r: -r["count"])
    save_sample("defaulters_by_region", region_summary)

    matrix_rows = [
        {"industry": i, "region": rg, "count": c, "total_amt": matrix_amt[(i, rg)]}
        for (i, rg), c in matrix.items()
    ]
    matrix_rows.sort(key=lambda r: -r["count"])
    save_sample("defaulters_industry_x_region", matrix_rows)


if __name__ == "__main__":
    main()

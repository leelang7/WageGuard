"""
업종 × 지역 단위 임금체불 위험점수 v0 (룰베이스)
- 입력: samples/defaulters.csv
- 신호:
    S1  cell_count_norm   : (업종,지역) 셀의 체불사업주 수 / 전체 평균 비율
    S2  avg_amt_norm      : 셀의 평균 체불액 / 전체 평균 비율
    S3  trend_norm        : 최근 2년(2025~2026) vs 이전 2년(2023~2024) 증감률
- 위험점수 = clip( 0.5·S1 + 0.3·S2 + 0.2·S3 , 0, 100 )
- 출력: samples/risk_score_v0.csv (셀별 점수 + 근거 신호)
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean

from analyze_defaulters import normalize_region, parse_round
from common import ROOT


def main() -> None:
    src = ROOT / "samples" / "defaulters.csv"
    rows: list[dict] = []
    with src.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r["amount_int"] = int(r["amount"]) if r["amount"].isdigit() else 0
            r["region"] = normalize_region(r["company_addr"])
            r["year"], _ = parse_round(r["round"])
            rows.append(r)

    cells = defaultdict(list)  # (industry, region) -> list[row]
    for r in rows:
        cells[(r["industry"], r["region"])].append(r)

    # 글로벌 평균
    global_avg_count = mean(len(v) for v in cells.values())
    global_avg_amt = mean(r["amount_int"] for r in rows)

    out_rows: list[dict] = []
    for (ind, reg), items in cells.items():
        cnt = len(items)
        amt_avg = mean(r["amount_int"] for r in items)
        prev = sum(1 for r in items if r["year"] in (2023, 2024))
        recent = sum(1 for r in items if r["year"] in (2025, 2026))
        trend = (recent - prev) / max(prev, 1)

        s1 = cnt / global_avg_count                              # 1.0 = 평균
        s2 = amt_avg / global_avg_amt
        s3 = max(-1.0, min(2.0, trend))                          # -1 ~ +2

        # 정규화: 1.0 기준 점수, max scaling은 사후
        score_raw = 0.5 * s1 + 0.3 * s2 + 0.2 * (s3 + 1) / 3 * 5  # s3는 0~5로 늘림
        out_rows.append(
            {
                "industry": ind,
                "region": reg,
                "count": cnt,
                "avg_amt": int(amt_avg),
                "prev_2y": prev,
                "recent_2y": recent,
                "trend": round(trend, 3),
                "S1_count": round(s1, 3),
                "S2_amt": round(s2, 3),
                "S3_trend": round(s3, 3),
                "score_raw": round(score_raw, 3),
            }
        )

    # 0~100 스케일링
    if out_rows:
        mx = max(r["score_raw"] for r in out_rows)
        for r in out_rows:
            r["risk_score"] = round(r["score_raw"] / mx * 100, 1)

    out_rows.sort(key=lambda r: -r["risk_score"])

    out = ROOT / "samples" / "risk_score_v0.csv"
    fields = [
        "risk_score", "industry", "region", "count", "avg_amt",
        "prev_2y", "recent_2y", "trend",
        "S1_count", "S2_amt", "S3_trend", "score_raw",
    ]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r[k] for k in fields})

    print("\n■ TOP 15 위험 셀 (업종 × 지역)")
    print(f"  {'점수':>5}  {'건수':>3}  {'평균체불':>12}  {'추세':>6}  업종 / 지역")
    for r in out_rows[:15]:
        print(
            f"  {r['risk_score']:>5}  {r['count']:>3}  {r['avg_amt']:>12,}  "
            f"{r['trend']:>+6.2f}  {r['industry']} / {r['region']}"
        )

    print(f"\n[+] 저장: {out.relative_to(ROOT)} ({len(out_rows)}건)")


if __name__ == "__main__":
    main()

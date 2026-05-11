"""
근로복지공단_고용 산재보험 가입 현황 (연간 스냅샷 파일)
- 데이터셋: data.go.kr 15002150 (fileData)
- 안내 페이지: https://www.data.go.kr/data/15002150/fileData.do
- 파일은 페이지에서 직접 다운로드 (CSV/Excel). API 호출 대상 아님.
- 본 스크립트는 다운받은 파일을 samples/ 아래에서 빠르게 둘러보는 용도.
"""

from __future__ import annotations

import csv
from pathlib import Path

from common import ROOT, save_sample

CANDIDATE_DIRS = [ROOT / "samples", ROOT / "data", ROOT]


def find_csv() -> Path | None:
    for d in CANDIDATE_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*고용*산재*가입*.csv"):
            return p
        for p in d.glob("*kcomwel*.csv"):
            return p
    return None


def main() -> None:
    f = find_csv()
    if not f:
        print(
            "[i] 파일이 아직 없습니다. data.go.kr 15002150 페이지에서\n"
            "    '근로복지공단_고용 산재보험 가입 현황_YYYY1231.csv' 를 다운받아\n"
            "    samples/ 아래에 두세요."
        )
        return

    print(f"[+] found: {f}")
    rows: list[dict] = []
    with f.open(encoding="cp949", errors="replace", newline="") as h:
        reader = csv.DictReader(h)
        for i, row in enumerate(reader):
            if i >= 5:
                break
            rows.append(row)

    print(f"[+] columns: {list(rows[0].keys()) if rows else []}")
    save_sample("kcomwel_file_head", rows)


if __name__ == "__main__":
    main()

"""
국민연금 가입사업장 CSV (data.go.kr 15083277) → SQLite 적재.

다운로드:
  https://www.data.go.kr/data/15083277/fileData.do
  파일명 예: 국민연금공단_국민연금 가입 사업장 내역_20260323.csv
  → samples/ 아래에 저장

CSV 컬럼 (정부 공개 표준):
  자료생성연월(YYYYMM), 사업장명, 사업자등록번호, 법정동주소, 도로명주소, 우편번호,
  사업장형태구분코드, 업종코드, 업종명, 적용일자, 재등록일자, 탈퇴일자,
  가입자수, 당월고지금액, 신규취득자수, 상실가입자수
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import conn, init_db   # noqa: E402

SAMPLES = ROOT / "samples"


def _normalize_name(name: str) -> str:
    s = re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", name or "")
    s = re.sub(r"^(주식회사|㈜|유한회사|합자회사|법인|\(주\))", "", s)
    s = s.replace("주식회사", "")
    return s.lower()


def _find_csv() -> Path | None:
    candidates = list(SAMPLES.glob("*국민연금*가입*.csv")) + list(SAMPLES.glob("*nps*workplaces*.csv"))
    return candidates[0] if candidates else None


def _open_with_encoding(p: Path):
    for enc in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            f = p.open(encoding=enc, errors="strict", newline="")
            f.read(1024); f.seek(0)
            return f, enc
        except UnicodeDecodeError:
            continue
    return p.open(encoding="cp949", errors="replace", newline=""), "cp949(replace)"


def _pick(row: dict, *keys: str) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k]).strip()
    return ""


def main() -> None:
    init_db()
    csv_path = _find_csv()
    if not csv_path:
        print("[!] CSV 파일이 samples/ 에 없습니다.")
        print("    https://www.data.go.kr/data/15083277/fileData.do 에서 다운로드 후")
        print("    samples/ 폴더에 두세요. 파일명에 '국민연금'/'가입' 들어가면 자동 인식.")
        return

    print(f"[+] 읽는 중: {csv_path.name}")
    f, enc = _open_with_encoding(csv_path)
    print(f"    encoding: {enc}")

    reader = csv.DictReader(f)
    headers = reader.fieldnames or []
    print(f"    columns: {headers[:10]}{'…' if len(headers) > 10 else ''}")

    inserted = 0
    skipped = 0
    with conn() as c:
        c.execute("DELETE FROM nps_workplaces")
        for row in reader:
            name = _pick(row, "사업장명", "wkpl_nm", "WKPL_NM")
            if not name:
                skipped += 1; continue
            bno = _pick(row, "사업자등록번호", "bzowrRgstNo", "bzowr_rgst_no", "BZOWR_RGST_NO").replace("-", "")
            addr_legal = _pick(row, "법정동주소", "ldongAddr", "ldong_addr")
            addr_road = _pick(row, "도로명주소", "rdnmAddr", "rdnm_addr")
            industry_nm = _pick(row, "업종명", "indutyNm", "induty_nm")
            sub_cnt = _pick(row, "가입자수", "jnngpCnt", "subscbr_cnt") or "0"
            new_cnt = _pick(row, "신규취득자수", "newSubscbrCnt", "new_cnt") or "0"
            lost_cnt = _pick(row, "상실가입자수", "lossSubscbrCnt", "lost_cnt") or "0"
            avg_pay = _pick(row, "당월고지금액", "notiAmt", "avrgPay") or "0"
            adpt_dt = _pick(row, "적용일자", "adptDt", "adpt_dt")
            snapshot = _pick(row, "자료생성연월", "snapshotYm")

            try:
                c.execute(
                    """INSERT INTO nps_workplaces
                       (wkpl_nm, wkpl_nm_norm, bzowr_rgst_no, addr, region_dg, region_sgg, region_emd,
                        industry, subscriber_cnt, new_cnt, lost_cnt, avg_pay, adpt_dt, snapshot_ym)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        name, _normalize_name(name), bno, addr_road or addr_legal,
                        addr_legal[:2] if addr_legal else "", "", "",
                        industry_nm,
                        int(sub_cnt) if sub_cnt.isdigit() else 0,
                        int(new_cnt) if new_cnt.isdigit() else 0,
                        int(lost_cnt) if lost_cnt.isdigit() else 0,
                        int(avg_pay) if avg_pay.isdigit() else 0,
                        adpt_dt, snapshot,
                    ),
                )
                inserted += 1
                if inserted % 50000 == 0:
                    print(f"    {inserted:,}건 적재…")
            except Exception as e:
                skipped += 1
    f.close()
    print(f"[+] 적재 완료: {inserted:,}건 (스킵 {skipped:,})")

    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        print(f"[+] DB nps_workplaces 총: {n:,}건")


if __name__ == "__main__":
    main()

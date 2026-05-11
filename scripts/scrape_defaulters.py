"""
체불사업주 명단 전체 스크레이핑
- 출처: https://www.moel.go.kr/info/defaulter/defaulterList.do
- POST 폼 + 페이지네이션
- 결과: samples/defaulters.csv (구분, 성명, 나이, 사업장명, 업종, 주소지, 소재지, 체불액원)
"""

from __future__ import annotations

import csv
import time

import requests
import urllib3
from bs4 import BeautifulSoup

from common import ROOT
from fetch_defaulter_list import LegacyCiphersAdapter, make_session


def request_with_retry(method, session, url, **kwargs):
    delays = [1, 2, 5, 10, 20]
    last = None
    for i, d in enumerate([0, *delays]):
        if d:
            print(f"  ↻ retry in {d}s …")
            time.sleep(d)
        try:
            r = session.request(method, url, **kwargs)
            if r.status_code in (200, 302):
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        print(f"  ! {last}")
    raise RuntimeError(f"failed after retries: {last}")

LIST_URL = "https://www.moel.go.kr/info/defaulter/defaulterList.do"
INTRO_URL = "https://www.moel.go.kr/info/defaulter/list.do"
PAGE_UNIT = 100


def parse_table(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "lxml")

    total_node = soup.select_one(".board_info .total b")
    total = int(total_node.text.strip()) if total_node else 0

    rows: list[dict] = []
    table = soup.select_one("table.defaulter-table")
    if not table:
        return rows, total

    for tr in table.select("tbody tr"):
        tds = tr.select("td.defaulter-td")
        if len(tds) < 8:
            continue
        rows.append(
            {
                "round":     tds[0].get_text(strip=True),
                "name":      tds[1].get_text(strip=True),
                "age":       tds[2].get_text(strip=True),
                "company":   (tds[3].get("title") or tds[3].get_text(strip=True)).strip(),
                "industry":  tds[4].get_text(strip=True),
                "owner_addr":   (tds[5].get("title") or tds[5].get_text(strip=True)).strip(),
                "company_addr": (tds[6].get("title") or tds[6].get_text(strip=True)).strip(),
                "amount":    tds[7].get_text(strip=True).replace(",", ""),
            }
        )
    return rows, total


def main() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = make_session()

    request_with_retry("GET", s, INTRO_URL, timeout=20, verify=False)
    s.headers["Referer"] = INTRO_URL

    all_rows: list[dict] = []
    page = 1
    total = None

    while True:
        body = {
            "pageIndex": str(page),
            "pageUnit": str(PAGE_UNIT),
            "searchOrder": "1",
            "searchYear": "",
            "searchField": "4",
            "searchText": "",
            "searchRegion": "",
            "searchIndustry": "",
        }
        r = request_with_retry("POST", s, LIST_URL, data=body, timeout=25, verify=False)
        rows, total = parse_table(r.text)
        if not rows:
            print(f"[page {page}] 0건 → 종료")
            break
        all_rows.extend(rows)
        print(f"[page {page}] +{len(rows)} (누적 {len(all_rows)} / 전체 {total})")

        if len(all_rows) >= total or len(rows) < PAGE_UNIT:
            break
        page += 1
        time.sleep(0.5)

    out = ROOT / "samples" / "defaulters.csv"
    fields = ["round", "name", "age", "company", "industry", "owner_addr", "company_addr", "amount"]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n[+] 저장: {out.relative_to(ROOT)} ({len(all_rows)}건)")


if __name__ == "__main__":
    main()

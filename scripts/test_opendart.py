"""
OpenDART 공시·재무 API 테스트
- 인증: opendart.fss.or.kr 인증키 (.env OPENDART_KEY)
- 호출 엔드포인트:
  1) 공시 목록 (list.json)            : 최근 공시
  2) 단일회사 주요계정 (fnlttSinglAcnt.json) : 재무제표 핵심계정
- 한계: 상장사 + 사업보고서 제출대상 비상장사만 커버. 영세사업장은 미수록.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from common import head, need, save_sample

LIST_API = "https://opendart.fss.or.kr/api/list.json"
FIN_API = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

# 삼성전자 공시 corp_code (DART 고유번호)
SAMSUNG_CORP_CODE = "00126380"


def fetch_recent_list(key: str) -> None:
    today = datetime.now()
    bgn = (today - timedelta(days=30)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    params = {
        "crtfc_key": key,
        "corp_code": SAMSUNG_CORP_CODE,
        "bgn_de": bgn,
        "end_de": end,
        "page_count": 5,
    }
    r = requests.get(LIST_API, params=params, timeout=15)
    print(f"[list status] {r.status_code}")
    print(f"[list head] {head(r.text, 600)}")
    try:
        save_sample("dart_list", r.json())
    except ValueError:
        save_sample("dart_list_raw", r.text, fmt="txt")


def fetch_main_accounts(key: str) -> None:
    year = datetime.now().year - 1
    params = {
        "crtfc_key": key,
        "corp_code": SAMSUNG_CORP_CODE,
        "bsns_year": str(year),
        "reprt_code": "11011",  # 사업보고서
    }
    r = requests.get(FIN_API, params=params, timeout=15)
    print(f"[fin status] {r.status_code}")
    print(f"[fin head] {head(r.text, 600)}")
    try:
        save_sample("dart_fin_accounts", r.json())
    except ValueError:
        save_sample("dart_fin_accounts_raw", r.text, fmt="txt")


def main() -> None:
    key = need("OPENDART_KEY")
    fetch_recent_list(key)
    fetch_main_accounts(key)


if __name__ == "__main__":
    main()

"""
EIS 고용행정통계 OpenAPI 테스트 (인증 불필요)
- 가이드: https://eis.work24.go.kr/eisps/opiv/selectOpivList.do
- 응답: XML (UTF-8)
- 3개 엔드포인트:
    OPIA 구인·구직 현황   https://eis.work24.go.kr/opi/joApi.do
    OPIB 피보험자          https://eis.work24.go.kr/opi/ipsApi.do
    OPIC 실업급여          https://eis.work24.go.kr/opi/uepsApi.do
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from common import head, redact_url, save_sample

ENDPOINTS = [
    ("opia_jobinfo",   "https://eis.work24.go.kr/opi/joApi.do",   "OPIA", "M"),
    ("opib_insurance", "https://eis.work24.go.kr/opi/ipsApi.do",  "OPIB", "1"),
    ("opic_unemploy",  "https://eis.work24.go.kr/opi/uepsApi.do", "OPIC", "1"),
]


def base_params(apisecd: str, sxds: str, ym: str = "202401") -> dict:
    return {
        "apiSecd": apisecd,
        "closStdrYm": ym,
        "rsdAreaCd": "11110",   # 서울 종로구
        "sxdsCd": sxds,
        "ageCd": "01",
        "rernSecd": "XML",
        "bgnPage": 1,
        "display": 20,
    }


def call(name: str, url: str, apisecd: str, sxds: str) -> None:
    print(f"\n--- {name} ({apisecd}) ---")
    r = requests.get(url, params=base_params(apisecd, sxds), timeout=15)
    r.encoding = r.encoding or "EUC-KR"
    print(f"[status] {r.status_code}")
    print(f"[final-url] {redact_url(r.url)}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[body-head] {head(r.text, 600)}")
    save_sample(f"eis_{name}", r.text, fmt="xml")


def main() -> None:
    for name, url, apisecd, sxds in ENDPOINTS:
        try:
            call(name, url, apisecd, sxds)
        except requests.RequestException as e:
            print(f"[!] {name} 호출 실패: {e}")


if __name__ == "__main__":
    main()

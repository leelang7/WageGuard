"""
근로복지공단 고용/산재보험 현황정보 OpenAPI 테스트
- 데이터셋: data.go.kr 15059256
- 엔드포인트(추정): http://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem
- 입력: 사업자번호 또는 사업장명 등
- 응답 형식: XML 또는 JSON (?_type=json)
"""

from __future__ import annotations

import requests

from common import head, need, redact_url, save_sample

API = "https://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem"

TEST_BNO = "1248100998"  # 삼성전자


def main() -> None:
    key = need("DATA_GO_KR_KEY")
    params = {
        "serviceKey": key,
        "_type": "json",
        "pageNo": 1,
        "numOfRows": 5,
    }

    r = requests.get(API, params=params, timeout=15)
    print(f"[status] {r.status_code}")
    print(f"[final-url] {redact_url(r.url)}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[body-head] {head(r.text, 800)}")

    try:
        data = r.json()
        save_sample("kcomwel_api", data)
    except ValueError:
        save_sample("kcomwel_api", r.text, fmt="xml")


if __name__ == "__main__":
    main()

"""
워크넷 채용정보 OpenAPI 테스트
- 데이터셋: data.go.kr 3038225 (한국고용정보원_워크넷 채용정보)
- 엔드포인트: http://openapi.work.go.kr/opi/opi/opia/wantedApi.do
- 인증: 별도 워크넷 OpenAPI 키 (.env WORKNET_KEY)
- 응답: XML
"""

from __future__ import annotations

import requests

from common import head, need, redact_url, save_sample

API = "http://openapi.work.go.kr/opi/opi/opia/wantedApi.do"


def main() -> None:
    key = need("WORKNET_KEY")
    params = {
        "authKey": key,
        "callTp": "L",       # L=목록
        "returnType": "XML",
        "startPage": 1,
        "display": 10,
        "region": "11000",   # 서울
    }

    r = requests.get(API, params=params, timeout=15)
    print(f"[status] {r.status_code}")
    print(f"[final-url] {redact_url(r.url)}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[body-head] {head(r.text, 800)}")

    save_sample("worknet_wanted", r.text, fmt="xml")


if __name__ == "__main__":
    main()

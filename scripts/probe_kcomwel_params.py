"""
kcomwel API의 검색 파라미터 후보 탐색.
체불사업주 명단의 1번 사업장(승화중공업)으로 여러 파라미터를 던져 보고 응답을 비교.
"""

from __future__ import annotations

import requests

from common import head, need, redact_url, save_sample

API = "https://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem"

CANDIDATES = [
    {},                                              # 베이스라인 (필터 없음)
    {"saeopjangNm": "승화중공업"},
    {"saeopjaNm": "승화중공업"},
    {"bplcNm": "승화중공업"},
    {"bzowrNm": "승화중공업"},
    {"bsnsNm": "승화중공업"},
    {"saeopjaDrno": "1248100998"},   # 삼성전자 사업자등록번호
    {"bzowrRgstNo": "1248100998"},
    {"saupjaNo": "1248100998"},
]


def main() -> None:
    key = need("DATA_GO_KR_KEY")
    base = {"serviceKey": key, "_type": "json", "pageNo": 1, "numOfRows": 5}
    for extra in CANDIDATES:
        params = {**base, **extra}
        try:
            r = requests.get(API, params=params, timeout=15)
            try:
                data = r.json()
                cnt = data.get("response", {}).get("body", {}).get("totalCount", "?")
                items = data.get("response", {}).get("body", {}).get("items", {})
                first = (items.get("item") or [{}])[0] if isinstance(items, dict) else {}
                hint = first.get("saeopjangNm", "")
            except ValueError:
                cnt, hint = "non-json", ""
            label = ", ".join(f"{k}={v}" for k, v in extra.items()) or "(no filter)"
            print(f"[{r.status_code}] {label:40s} totalCount={cnt}  hit0={hint!r}")
        except Exception as e:
            print(f"[err] {extra}: {e}")


if __name__ == "__main__":
    main()

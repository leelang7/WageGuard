"""
국세청 사업자등록 상태조회 API 테스트
- 데이터셋: data.go.kr 15081808
- 엔드포인트: https://api.odcloud.kr/api/nts-businessman/v1/status
- 입력: 사업자번호 (10자리, 하이픈 제거) 리스트
- 출력: b_stt(영업/휴업/폐업), tax_type, end_dt 등
"""

from __future__ import annotations

import requests
from common import head, need, save_sample

API = "https://api.odcloud.kr/api/nts-businessman/v1/status"

# 테스트용 사업자번호 (잘 알려진 정상 사업장 + 임의 케이스)
TEST_BNOS = [
    "1248100998",  # 삼성전자
    "1018109147",  # 현대자동차
    "1208147521",  # 카카오
    "0000000000",  # 존재하지 않는 번호 (에러 케이스)
]


def main() -> None:
    key = need("DATA_GO_KR_KEY")
    params = {"serviceKey": key, "returnType": "JSON"}
    body = {"b_no": TEST_BNOS}

    r = requests.post(API, params=params, json=body, timeout=15)
    print(f"[status] {r.status_code}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[body-head] {head(r.text)}")

    try:
        data = r.json()
    except ValueError:
        save_sample("nts_status_error", r.text, fmt="txt")
        return

    save_sample("nts_status", data)

    if isinstance(data, dict) and data.get("code") == -4:
        print(
            "[!] '등록되지 않은 인증키' — data.go.kr에서 본 데이터셋(15081808)에\n"
            "    개별 '활용신청' 이 필요합니다. 일반 인증키만으론 호출 안 됩니다.\n"
            "    https://www.data.go.kr/data/15081808/openapi.do 에서 활용신청 → 승인 후 재시도."
        )
        return

    if isinstance(data, dict) and "data" in data:
        for row in data["data"]:
            print(f"  - {row.get('b_no')}: {row.get('b_stt')} / {row.get('tax_type')}")


if __name__ == "__main__":
    main()

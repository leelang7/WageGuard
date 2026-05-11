"""
체불사업주 명단 페이지 스크래핑 (인증 불필요)
- 출처: https://www.moel.go.kr/info/defaulter/defaulterList.do
- 근거: 근로기준법 §43-2
- 가공이 까다로움:
    * SSL: 레거시 cipher 강제
    * 세션: 안내 페이지(list.do) 먼저 방문 후 쿠키 보유 상태로 명단 페이지 GET
    * 봇 차단 회피: 정상 브라우저 헤더 흉내
"""

from __future__ import annotations

import ssl
import urllib3
from urllib3.util import create_urllib3_context

import requests
from requests.adapters import HTTPAdapter

from common import head, save_sample

INTRO_URL = "https://www.moel.go.kr/info/defaulter/list.do"
LIST_URL = "https://www.moel.go.kr/info/defaulter/defaulterList.do"


class LegacyCiphersAdapter(HTTPAdapter):
    """오래된 한국 정부사이트 대응: SECLEVEL 낮춰 약한 cipher 허용."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers="DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
    )
    s.mount("https://", LegacyCiphersAdapter())
    return s


def main() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = make_session()

    # 1) 안내 페이지 방문 (세션 쿠키 확보)
    r0 = s.get(INTRO_URL, timeout=20, verify=False)
    print(f"[intro status] {r0.status_code}, cookies: {len(s.cookies)}")

    # 2) 명단 페이지
    s.headers["Referer"] = INTRO_URL
    r = s.get(LIST_URL, timeout=20, verify=False)
    print(f"[list status] {r.status_code}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[bytes] {len(r.content)}")
    print(f"[head] {head(r.text, 800)}")
    save_sample("defaulter_list", r.text, fmt="html")


if __name__ == "__main__":
    main()

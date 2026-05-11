"""
한국언론진흥재단 BIGKINDS API 테스트
- 인증: BIGKINDS API 키 (.env BIGKINDS_KEY)
- 엔드포인트: https://tools.kinds.or.kr/search/news
- 임금체불·해당 사업장명 키워드로 뉴스 검색
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from common import head, need, save_sample

API = "https://tools.kinds.or.kr/search/news"


def main() -> None:
    key = need("BIGKINDS_KEY")
    today = datetime.now()
    bgn = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    body = {
        "access_key": key,
        "argument": {
            "query": "임금체불",
            "published_at": {"from": bgn, "until": end},
            "sort": {"date": "desc"},
            "return_from": 0,
            "return_size": 10,
            "fields": [
                "news_id",
                "title",
                "content",
                "published_at",
                "provider",
                "category",
                "byline",
            ],
        },
    }

    r = requests.post(API, json=body, timeout=20)
    print(f"[status] {r.status_code}")
    print(f"[content-type] {r.headers.get('content-type')}")
    print(f"[body-head] {head(r.text, 800)}")

    try:
        data = r.json()
        save_sample("bigkinds_search", data)
        docs = data.get("return_object", {}).get("documents", []) or []
        print(f"[+] hits: {len(docs)}")
        for d in docs[:3]:
            print(f"  - {d.get('published_at')} | {d.get('provider')} | {d.get('title')}")
    except ValueError:
        save_sample("bigkinds_raw", r.text, fmt="txt")


if __name__ == "__main__":
    main()

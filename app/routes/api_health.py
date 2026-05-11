from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api")

CATALOG = [
    {"name": "국세청 사업자상태조회",         "org": "국세청",         "endpoint": "https://api.odcloud.kr/api/nts-businessman/v1/status",                                  "status": "정상"},
    {"name": "근로복지공단 고용/산재 현황",    "org": "근로복지공단",   "endpoint": "https://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem",         "status": "정상"},
    {"name": "EIS OPIA 구인구직",              "org": "한국고용정보원", "endpoint": "https://eis.work24.go.kr/opi/joApi.do",                                                "status": "정상"},
    {"name": "EIS OPIB 피보험자",              "org": "한국고용정보원", "endpoint": "https://eis.work24.go.kr/opi/ipsApi.do",                                               "status": "정상"},
    {"name": "EIS OPIC 실업급여",              "org": "한국고용정보원", "endpoint": "https://eis.work24.go.kr/opi/uepsApi.do",                                              "status": "정상"},
    {"name": "체불사업주 명단(스크래핑)",      "org": "고용노동부",     "endpoint": "https://www.moel.go.kr/info/defaulter/defaulterList.do",                               "status": "정상"},
    {"name": "OpenDART",                       "org": "금융감독원",     "endpoint": "https://opendart.fss.or.kr/api/list.json",                                              "status": "대기"},
]


@router.get("/health")
def health() -> dict:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    with conn() as c:
        recent = c.execute("SELECT COUNT(*) FROM api_calls WHERE called_at >= ?", (cutoff,)).fetchone()[0]
        ok_n = c.execute("SELECT COUNT(*) FROM api_calls WHERE called_at >= ? AND ok=1", (cutoff,)).fetchone()[0]
        logs = c.execute(
            "SELECT api, endpoint, status, duration_ms, ok, called_at FROM api_calls ORDER BY id DESC LIMIT 30"
        ).fetchall()
    return {
        "sources": CATALOG,
        "recent_24h": recent,
        "success_pct": (ok_n / recent * 100) if recent else 100.0,
        "logs": [dict(r) for r in logs],
    }

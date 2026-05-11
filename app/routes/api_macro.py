"""EIS 고용행정통계 OpenAPI 프록시 + 캐시"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import requests
from fastapi import APIRouter

from ..db import conn
from .api_business import log_call

router = APIRouter(prefix="/api/macro")

EIS_OPIC = "https://eis.work24.go.kr/opi/uepsApi.do"

# 광역시도 → 대표 시군구 코드 (5자리)
REGION_TO_AREA = {
    "서울": ("11110", "서울 종로구"),
    "부산": ("26110", "부산 중구"),
    "대구": ("27110", "대구 중구"),
    "인천": ("28110", "인천 중구"),
    "광주": ("29110", "광주 동구"),
    "대전": ("30110", "대전 동구"),
    "울산": ("31110", "울산 중구"),
    "세종": ("36110", "세종"),
    "경기": ("41110", "경기 수원시 장안구"),
    "강원": ("42110", "강원 춘천시"),
    "충북": ("43110", "충북 청주시 상당구"),
    "충남": ("44131", "충남 천안시 동남구"),
    "전북": ("45110", "전북 전주시 완산구"),
    "전남": ("46110", "전남 목포시"),
    "경북": ("47111", "경북 포항시 남구"),
    "경남": ("48121", "경남 창원시 의창구"),
    "제주": ("50110", "제주 제주시"),
}

YEAR_MONTHS = ["202401", "202404", "202407", "202410"]


def call_eis_opic(area: str, ym: str) -> tuple[dict | None, int]:
    t0 = time.time()
    params = {
        "apiSecd": "OPIC",
        "closStdrYm": ym,
        "rsdAreaCd": area,
        "sxdsCd": "1",
        "ageCd": "01",
        "rernSecd": "XML",
        "bgnPage": 1,
        "display": 20,
    }
    try:
        r = requests.get(EIS_OPIC, params=params, timeout=12)
        r.encoding = r.encoding or "EUC-KR"
        dt = int((time.time() - t0) * 1000)
        log_call("EIS-OPIC", EIS_OPIC, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, dt

        root = ET.fromstring(r.text)
        items = []
        for rqst in root.findall(".//rqst"):
            items.append({el.tag: (el.text or "") for el in rqst})
        return {"items": items}, dt
    except Exception:
        dt = int((time.time() - t0) * 1000)
        log_call("EIS-OPIC", EIS_OPIC, 0, dt, False)
        return None, dt


def cached_macro(region: str, ym: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT payload FROM macro_eis WHERE region = ? AND year_month = ? AND kind = 'OPIC'",
            (region, ym),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def cache_macro(region: str, ym: str, payload: dict) -> None:
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO macro_eis (region, year_month, kind, payload, fetched_at)
               VALUES (?,?,?,?,?)""",
            (region, ym, "OPIC", json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )


@router.get("/{region}")
def macro(region: str) -> dict:
    if region not in REGION_TO_AREA:
        return {"error": f"지원되지 않는 지역: {region}"}
    area, label = REGION_TO_AREA[region]

    series_pmnt = []
    series_rqut = []
    total_ms = 0

    for ym in YEAR_MONTHS:
        cached = cached_macro(region, ym)
        if cached:
            payload = cached
        else:
            t0 = time.time()
            payload, dt = call_eis_opic(area, ym)
            total_ms += dt
            if payload:
                cache_macro(region, ym, payload)
            else:
                payload = {"items": []}

        pmam = sum(int(x.get("uepsPmam", "0") or "0") for x in payload["items"])
        rqut = sum(int(x.get("uepsRqutNmpr", "0") or "0") for x in payload["items"])
        series_pmnt.append(pmam)
        series_rqut.append(rqut)

    return {
        "region_label": label,
        "year_month": " ~ ".join([YEAR_MONTHS[0], YEAR_MONTHS[-1]]),
        "fetched_in_ms": total_ms,
        "x": YEAR_MONTHS,
        "series": [
            {"name": "실업급여 지급액", "data": series_pmnt},
            {"name": "수급자격 신청자수", "data": series_rqut},
        ],
    }

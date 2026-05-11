"""운영 콘솔 — 시스템 운용 상태 라이브 노출.

판정 기준: "이쁜 보고서"가 아닌 "동작하는 시스템"임을 증명.
- 이벤트 스트림 (실시간)
- DB 테이블 행수
- 스케줄러 상태
- 시스템 시작 시각·업타임
- 점검 적재 통계
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from fastapi import APIRouter

from ..db import conn
from ..events import log_event
from ..middleware import get_metrics

router = APIRouter(prefix="/api/ops")

_STARTED_AT = time.time()


@router.get("/uptime")
def uptime() -> dict:
    """시스템 업타임."""
    secs = int(time.time() - _STARTED_AT)
    days = secs // 86400
    hrs = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    s = secs % 60
    return {
        "started_at_unix": int(_STARTED_AT),
        "uptime_seconds": secs,
        "uptime_human": f"{days}d {hrs:02d}h {mins:02d}m {s:02d}s" if days else f"{hrs:02d}h {mins:02d}m {s:02d}s",
    }


@router.get("/db-counts")
def db_counts() -> dict:
    """주요 DB 테이블 행수."""
    tables = [
        "defaulters", "risk_cells", "cases", "case_events",
        "company_signals", "watchlist", "watchlist_events",
        "notifications", "owner_attestations", "owner_subscriptions",
        "system_events", "inspections", "api_calls",
    ]
    result = {}
    with conn() as c:
        for t in tables:
            try:
                n = c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                result[t] = n
            except Exception:
                result[t] = None
    return {"tables": result}


@router.get("/events")
def events(limit: int = 50, kind: str | None = None) -> dict:
    """이벤트 스트림 — 최근 N건."""
    with conn() as c:
        if kind:
            rows = c.execute(
                "SELECT kind, severity, actor, summary, payload, duration_ms, created_at "
                "FROM system_events WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT kind, severity, actor, summary, payload, duration_ms, created_at "
                "FROM system_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    events = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except Exception:
            payload = {}
        events.append({
            "kind": r["kind"],
            "severity": r["severity"],
            "actor": r["actor"],
            "summary": r["summary"],
            "payload": payload,
            "duration_ms": r["duration_ms"],
            "created_at": r["created_at"],
        })
    return {"events": events, "count": len(events)}


@router.get("/scheduler")
def scheduler_status() -> dict:
    """스케줄러 작업 상태 — 다음 실행 예정·최근 결과."""
    with conn() as c:
        last_heartbeat = c.execute(
            "SELECT created_at, payload FROM system_events "
            "WHERE kind = 'heartbeat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_ingest = c.execute(
            "SELECT created_at, summary, duration_ms FROM system_events "
            "WHERE kind = 'ingest' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_train = c.execute(
            "SELECT created_at, summary, duration_ms FROM system_events "
            "WHERE kind = 'model_train' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def fmt(row):
        if not row:
            return None
        return dict(row)

    return {
        "jobs": [
            {
                "name": "heartbeat",
                "interval_sec": 30,
                "last_run": fmt(last_heartbeat),
            },
            {
                "name": "ingest_check",
                "interval_sec": 300,
                "last_run": fmt(last_ingest),
            },
            {
                "name": "model_retrain",
                "interval_sec": 3600,
                "last_run": fmt(last_train),
            },
        ],
    }


@router.get("/health")
def health() -> dict:
    """시스템 헬스체크 — 모든 핵심 부품."""
    checks = []
    # DB
    try:
        with conn() as c:
            c.execute("SELECT 1").fetchone()
        checks.append({"component": "sqlite", "ok": True, "detail": str(os.path.basename(os.getenv("DB_PATH", "WageGuard.sqlite")))})
    except Exception as e:
        checks.append({"component": "sqlite", "ok": False, "detail": str(e)})

    # 환경 변수
    checks.append({"component": "DATA_GO_KR_KEY", "ok": bool(os.getenv("DATA_GO_KR_KEY"))})
    checks.append({"component": "WORK24_AUTH_KEY_JOB", "ok": bool(os.getenv("WORK24_AUTH_KEY_JOB"))})
    checks.append({"component": "WORK24_AUTH_KEY_DUTY", "ok": bool(os.getenv("WORK24_AUTH_KEY_DUTY"))})
    checks.append({"component": "WORK24_AUTH_KEY_TRAINING", "ok": bool(os.getenv("WORK24_AUTH_KEY_TRAINING"))})
    checks.append({"component": "WORK24_AUTH_KEY_CAREER", "ok": bool(os.getenv("WORK24_AUTH_KEY_CAREER"))})

    return {
        "ok": all(c["ok"] for c in checks if c.get("ok") is not None),
        "checks": checks,
        "uptime": uptime(),
    }


@router.get("/status")
def status() -> dict:
    """운영 콘솔 핵심 상태 요약 alias."""
    return {
        "uptime": uptime(),
        "health": health(),
        "db_counts": db_counts(),
        "scheduler": scheduler_status(),
        "metrics": metrics(),
    }


@router.get("/metrics")
def metrics() -> dict:
    """HTTP 요청 메트릭 — 카운트·latency·에러율·상위 경로."""
    m = get_metrics()
    return m.stats() if m else {"error": "metrics middleware not installed"}


@router.get("/timeseries")
def timeseries(window_min: int = 30) -> dict:
    """이벤트 시계열 — 최근 N분간 분당 발생 카운트 (kind별)."""
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(minutes=window_min)).isoformat(timespec="seconds") + "Z"
    with conn() as c:
        rows = c.execute(
            "SELECT kind, created_at FROM system_events WHERE created_at >= ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()

    # 분 단위 버킷
    buckets: dict[str, dict[str, int]] = {}
    kinds = set()
    for r in rows:
        # YYYY-MM-DDTHH:MM
        bucket = r["created_at"][:16]
        kind = r["kind"]
        kinds.add(kind)
        if bucket not in buckets:
            buckets[bucket] = {}
        buckets[bucket][kind] = buckets[bucket].get(kind, 0) + 1

    sorted_buckets = sorted(buckets.keys())
    series = {}
    for kind in kinds:
        series[kind] = [buckets[b].get(kind, 0) for b in sorted_buckets]

    return {
        "buckets": sorted_buckets,
        "series": series,
        "total": len(rows),
    }


@router.get("/recent-requests")
def recent_requests(limit: int = 20) -> dict:
    """최근 N개 요청 — 라이브 시스템 활동 증거."""
    m = get_metrics()
    if not m:
        return {"error": "metrics middleware not installed"}
    return {"recent": m.recent_requests(limit), "count": limit}


@router.post("/test-event")
def test_event() -> dict:
    """수동 이벤트 발생 — 평가위원이 직접 트리거 가능."""
    log_event("manual", "평가위원 수동 트리거", actor="user", payload={"trigger_at": datetime.utcnow().isoformat() + "Z"})
    return {"logged": True}


@router.get("/event-stream")
async def event_stream() -> "StreamingResponse":
    """SSE — 새 이벤트가 발생하면 실시간 푸시 (long-polling)."""
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def gen():
        last_id = 0
        # 첫 호출 시 최근 ID 기준
        with conn() as c:
            row = c.execute("SELECT MAX(id) AS m FROM system_events").fetchone()
            last_id = row["m"] or 0

        # SSE 초기 메시지
        yield f"event: connected\ndata: {{\"last_id\": {last_id}}}\n\n"

        # 5초 간격으로 새 이벤트 폴링 (300초 후 자동 종료)
        for _ in range(60):
            await asyncio.sleep(5)
            with conn() as c:
                rows = c.execute(
                    "SELECT id, kind, severity, actor, summary, created_at "
                    "FROM system_events WHERE id > ? ORDER BY id ASC LIMIT 50",
                    (last_id,),
                ).fetchall()
            for r in rows:
                last_id = r["id"]
                payload = {
                    "id": r["id"],
                    "kind": r["kind"],
                    "severity": r["severity"],
                    "actor": r["actor"],
                    "summary": r["summary"],
                    "created_at": r["created_at"],
                }
                yield f"event: event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

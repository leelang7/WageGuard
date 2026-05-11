"""시스템 이벤트 로깅 — DB system_events 테이블 적재."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .db import conn


def log_event(kind: str, summary: str, *,
              severity: str = "info",
              actor: str = "system",
              payload: dict[str, Any] | None = None,
              duration_ms: int | None = None) -> None:
    """시스템 이벤트 기록."""
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO system_events (kind, severity, actor, summary, payload, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind, severity, actor, summary,
                    json.dumps(payload or {}, ensure_ascii=False),
                    duration_ms,
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                ),
            )
    except Exception as e:
        # 이벤트 로깅 실패가 시스템을 막으면 안 됨
        print(f"[events] log_event failed: {e}")


class TimedEvent:
    """with TimedEvent('ingest', '체불명단 적재'): ... 패턴."""
    def __init__(self, kind: str, summary: str, **kw):
        self.kind = kind
        self.summary = summary
        self.kw = kw
        self.start = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ms = int((time.time() - self.start) * 1000)
        kw = dict(self.kw)
        if exc_type:
            kw["severity"] = "error"
            kw["payload"] = {**(kw.get("payload") or {}), "error": str(exc_val)}
        log_event(self.kind, self.summary, duration_ms=ms, **kw)
        return False  # don't suppress

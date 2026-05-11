"""HTTP 미들웨어 — 모든 요청 자동 카운트·latency·에러율.

운영 콘솔에 라이브 메트릭 노출.
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware


class _MetricsState:
    """모듈 싱글톤 — 미들웨어 인스턴스와 무관하게 누적."""

    def __init__(self, window_size: int = 500):
        self.window_size = window_size
        self.recent: deque = deque(maxlen=window_size)
        self.total_count = 0
        self.total_errors = 0
        self.path_counts: dict[str, int] = {}
        self._lock = Lock()

    def record(self, path: str, status: int, elapsed_ms: int) -> None:
        with self._lock:
            self.total_count += 1
            if status >= 500:
                self.total_errors += 1
            self.path_counts[path] = self.path_counts.get(path, 0) + 1
            self.recent.append({
                "path": path,
                "status": status,
                "duration_ms": elapsed_ms,
                "ts": int(time.time()),
            })

    def stats(self) -> dict:
        with self._lock:
            n = len(self.recent)
            if n == 0:
                return {
                    "total_requests": self.total_count,
                    "total_errors": self.total_errors,
                    "recent_count": 0,
                    "p50_ms": None, "p95_ms": None, "p99_ms": None,
                    "error_rate": 0.0,
                    "top_paths": [],
                }
            durations = sorted(r["duration_ms"] for r in self.recent)
            errors_recent = sum(1 for r in self.recent if r["status"] >= 500)
            p50 = durations[n // 2]
            p95 = durations[min(n - 1, int(n * 0.95))]
            p99 = durations[min(n - 1, int(n * 0.99))]
            top = sorted(self.path_counts.items(), key=lambda x: -x[1])[:10]
            return {
                "total_requests": self.total_count,
                "total_errors": self.total_errors,
                "recent_count": n,
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
                "error_rate": round(errors_recent / n, 3),
                "top_paths": [{"path": p, "count": c} for p, c in top],
            }

    def recent_requests(self, limit: int = 30) -> list[dict]:
        with self._lock:
            return list(self.recent)[-limit:][::-1]


_STATE = _MetricsState()


def get_metrics() -> _MetricsState:
    return _STATE


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        path = request.url.path
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed_ms = int((time.time() - start) * 1000)
            _STATE.record(path, status, elapsed_ms)

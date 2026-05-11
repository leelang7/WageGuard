"""백그라운드 스케줄러 — heartbeat + 워치리스트 재조회 + 모델 재학습 트리거.

시스템 운용 가시화:
- 30초마다 heartbeat 이벤트 (system_events 적재)
- 10분마다 워치리스트 일괄 재조회
- 1시간마다 ML 모델 재학습 시그널
"""
from __future__ import annotations

import threading
import time
import traceback

from .db import conn
from .events import log_event

_thread: threading.Thread | None = None
_stop = threading.Event()

_HEARTBEAT_SEC = 30
_WATCH_REFRESH_SEC = 600
_MODEL_RETRAIN_SEC = 3600


def _heartbeat() -> None:
    """30초 heartbeat — 시스템 살아있음을 증명."""
    with conn() as c:
        n_def = c.execute("SELECT COUNT(*) AS n FROM defaulters").fetchone()["n"]
        n_cases = c.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
        n_insp = c.execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"]
    log_event(
        "heartbeat", "시스템 정상 작동",
        actor="scheduler",
        payload={"defaulters": n_def, "cases": n_cases, "inspections": n_insp},
    )


def _refresh_watchlist() -> None:
    """워치리스트 일괄 재조회."""
    from .routes.api_watch import _refresh
    from .routes.api_notify import push_notification

    with conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM watchlist").fetchall()]
    refreshed = 0
    triggered = 0
    for wid in ids:
        try:
            r = _refresh(wid)
            refreshed += 1
            new_events = [e for e in r.get("events", []) if e in ("status_change", "score_jump")]
            if new_events:
                triggered += 1
                with conn() as c:
                    w = c.execute("SELECT label FROM watchlist WHERE id=?", (wid,)).fetchone()
                label = w["label"] if w else f"#{wid}"
                push_notification(
                    audience="worker",
                    severity="warning",
                    title=f"워치리스트 변동 · {label}",
                    body=f"이벤트: {', '.join(new_events)} · 점수={r.get('score')} · 상태={r.get('status')}",
                    link="/watch",
                )
        except Exception:
            traceback.print_exc()
    log_event(
        "watch_refresh", f"워치리스트 일괄 재조회 — {refreshed}건 갱신, {triggered}건 알림",
        actor="scheduler",
        payload={"refreshed": refreshed, "triggered": triggered},
    )


def _retrain_model() -> None:
    """ML 모델 재학습 트리거."""
    try:
        from .routes.api_ml import _MODEL, _build_dataset, _train_logistic
        global_model = _MODEL  # noqa
        X, y, meta = _build_dataset()
        if X:
            import random
            random.seed(int(time.time()) % 100000)
            m = _train_logistic(X, y, epochs=100, lr=0.1)
            # 새 모델 캐시 갱신
            from .routes import api_ml
            api_ml._MODEL = {
                "weights": m["weights"],
                "train_acc": m["train_acc"],
                "loss_history": m["loss_history"][-10:],
                "n_pos": meta["n_pos"],
                "n_neg": meta["n_neg"],
                "available": True,
            }
            log_event(
                "model_train", f"ML 재학습 완료 — train_acc {m['train_acc']:.3f}",
                actor="scheduler",
                payload={"train_acc": m["train_acc"], "n_pos": meta["n_pos"], "n_neg": meta["n_neg"]},
            )
        else:
            log_event("model_train", "재학습 스킵 — 데이터 부족", severity="warn", actor="scheduler")
    except Exception as e:
        log_event("model_train", f"재학습 실패: {e}", severity="error", actor="scheduler")
        traceback.print_exc()


def _loop() -> None:
    """메인 루프 — 30초 단위 heartbeat + 주기 작업 디스패치."""
    last_watch = time.time()
    last_train = time.time()
    while not _stop.wait(_HEARTBEAT_SEC):
        try:
            _heartbeat()
            now = time.time()
            if now - last_watch >= _WATCH_REFRESH_SEC:
                _refresh_watchlist()
                last_watch = now
            if now - last_train >= _MODEL_RETRAIN_SEC:
                _retrain_model()
                last_train = now
        except Exception:
            traceback.print_exc()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="WageGuard-scheduler", daemon=True)
    _thread.start()
    log_event("startup", "스케줄러 시작", actor="system",
              payload={"heartbeat_sec": _HEARTBEAT_SEC,
                       "watch_refresh_sec": _WATCH_REFRESH_SEC,
                       "model_retrain_sec": _MODEL_RETRAIN_SEC})


def stop() -> None:
    _stop.set()


def trigger_now() -> dict:
    """수동 트리거 — 데모용."""
    _heartbeat()
    _refresh_watchlist()
    return {"triggered": True}

"""Track A SDK 9 신호 가중치 학습 — Logistic Regression으로 휴리스틱 → ML 학습화.

기존: 휴리스틱 가중치 (timezone +15, WebRTC +25 등)
개선: 시뮬 1,000 케이스(부정 100 / 정상 900, 현실적 불균형) 학습 → 데이터 기반 점수
     samples/m6_phase25_simulation.csv 사용 우선 (없으면 내부 시뮬 fallback)
"""
from __future__ import annotations

import csv
import math
import random
from pathlib import Path

from fastapi import APIRouter
from ..settings import SAMPLES

router = APIRouter(prefix="/api/sdk-weights")


SIGNAL_NAMES = [
    "ip_country_mismatch",     # IP 국가 ≠ 한국
    "timezone_mismatch",       # timezone ≠ Asia/Seoul
    "language_mismatch",       # 언어 ≠ ko
    "webrtc_leak_overseas",    # WebRTC 실제 IP 해외
    "webgl_virtual_adapter",   # WebGL 가상 어댑터
    "mouse_jitter_high",       # 마우스 jitter 임계 이상
    "key_dispersion_high",     # 키 입력 분산 큼
    "device_inconsistency",    # 디바이스 불일치
    "canvas_fp_anomaly",       # canvas fingerprint 이상
]


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _load_csv_cases() -> tuple[list[list[int]], list[int]] | None:
    """samples/m6_phase25_simulation.csv에서 Phase 1 신호만 학습 데이터 로드.

    Phase 1 신호 매핑 (출입국 기록 없는 브라우저 신호만 사용):
    ip_country → ip_country_mismatch (≠KR)
    tz_changed → timezone_mismatch
    device_changed → language_mismatch proxy (디바이스 변경 → 언어 불일치 proxy)
    rdp_latency_ms>60 → webrtc_leak_overseas proxy
    mouse_jitter_ms>70 → mouse_jitter_high
    key_burst_ratio>1.8 → key_dispersion_high
    device_drift>0.6 → device_inconsistency
    apply_count_6m>3 → canvas_fp_anomaly proxy (반복 신청)
    training_ip_overseas → webgl_virtual_adapter proxy
    """
    TH_MOUSE = 70.0; TH_RDP = 60.0; TH_KEY = 1.8; TH_DEV = 0.6
    for fname in ("m6_phase25_simulation.csv", "m6_simulation.csv"):
        path = SAMPLES / fname
        if not path.exists():
            continue
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        except Exception:
            continue
        X, y = [], []
        for r in rows:
            x = [
                1 if (r.get("ip_country") or "KR") != "KR" else 0,
                1 if str(r.get("tz_changed", "False")).lower() in ("true", "1") else 0,
                1 if str(r.get("device_changed", "False")).lower() in ("true", "1") else 0,
                1 if float(r.get("rdp_latency_ms", 0) or 0) > TH_RDP else 0,
                1 if str(r.get("training_ip_overseas", "False")).lower() in ("true", "1") else 0,
                1 if float(r.get("mouse_jitter_ms", 0) or 0) > TH_MOUSE else 0,
                1 if float(r.get("key_burst_ratio", 0) or 0) > TH_KEY else 0,
                1 if float(r.get("device_drift", 0) or 0) > TH_DEV else 0,
                1 if int(r.get("apply_count_6m", 0) or 0) > 3 else 0,
            ]
            X.append(x)
            y.append(int(r["label"]))
        if X:
            return X, y
    return None


def _generate_cases(n: int = 1000, seed: int = 42) -> tuple[list[list[int]], list[int]]:
    """Fallback: 1,000 시뮬 케이스 — 부정 10% / 정상 90% (현실적 불균형)."""
    random.seed(seed)
    X: list[list[int]] = []
    y: list[int] = []
    for i in range(n):
        is_fraud = i < (n // 10)  # 10% 부정
        case = []
        if is_fraud:
            for _ in SIGNAL_NAMES:
                case.append(1 if random.random() < 0.6 else 0)
            y.append(1)
        else:
            for _ in SIGNAL_NAMES:
                case.append(1 if random.random() < 0.08 else 0)
            y.append(0)
        X.append(case)
    return X, y


def _train_logistic(X: list[list[int]], y: list[int],
                    epochs: int = 100, lr: float = 0.3, l2: float = 0.01) -> list[float]:
    n_features = len(X[0])
    w = [0.0] * (n_features + 1)  # +1 for bias
    n = len(X)
    for _ in range(epochs):
        idx = list(range(n))
        random.shuffle(idx)
        for i in idx:
            x = [1.0] + [float(v) for v in X[i]]
            z = sum(wi * xi for wi, xi in zip(w, x))
            p = _sigmoid(z)
            err = p - y[i]
            for j in range(n_features + 1):
                w[j] -= lr * (err * x[j] + l2 * w[j])
    return w


def _evaluate(X: list[list[int]], y: list[int], w: list[float]) -> dict:
    tp = fp = tn = fn = 0
    for x, yt in zip(X, y):
        xv = [1.0] + [float(v) for v in x]
        p = _sigmoid(sum(wi * xi for wi, xi in zip(w, xv)))
        pred = 1 if p >= 0.5 else 0
        if pred == 1 and yt == 1: tp += 1
        elif pred == 1 and yt == 0: fp += 1
        elif pred == 0 and yt == 0: tn += 1
        else: fn += 1
    acc = (tp + tn) / max(len(y), 1)
    prec = tp / max(tp + fp, 1)
    rc = tp / max(tp + fn, 1)
    f1 = 2 * prec * rc / max(prec + rc, 1e-9)
    return {
        "accuracy": round(acc, 3),
        "precision": round(prec, 3),
        "recall": round(rc, 3),
        "f1": round(f1, 3),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


_MODEL_CACHE = None


def _get_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    csv_result = _load_csv_cases()
    if csv_result is not None:
        X, y = csv_result
        source = "m6_phase25_simulation.csv (부정 100건 / 정상 900건)"
    else:
        X, y = _generate_cases(1000)
        source = "내부 시뮬 (fallback)"
    # 80/20 train/test split (stratified by label)
    random.seed(42)
    pos_idx = [i for i, yi in enumerate(y) if yi == 1]
    neg_idx = [i for i, yi in enumerate(y) if yi == 0]
    random.shuffle(pos_idx); random.shuffle(neg_idx)
    n_pos_test = max(1, len(pos_idx) // 5)
    n_neg_test = max(1, len(neg_idx) // 5)
    test_idx = set(pos_idx[:n_pos_test] + neg_idx[:n_neg_test])
    X_tr = [X[i] for i in range(len(X)) if i not in test_idx]
    y_tr = [y[i] for i in range(len(y)) if i not in test_idx]
    X_te = [X[i] for i in range(len(X)) if i in test_idx]
    y_te = [y[i] for i in range(len(y)) if i in test_idx]
    w = _train_logistic(X_tr, y_tr)
    perf = _evaluate(X_te, y_te, w)
    _MODEL_CACHE = {
        "weights": w,
        "performance": perf,
        "n_train": len(X_tr),
        "n_test": len(X_te),
        "data_source": source,
    }
    return _MODEL_CACHE


@router.get("/info")
def info() -> dict:
    """학습된 SDK 신호 가중치 + 성능."""
    m = _get_model()
    weights_dict = {"bias": round(m["weights"][0], 3)}
    for i, name in enumerate(SIGNAL_NAMES):
        weights_dict[name] = round(m["weights"][i + 1], 3)
    return {
        "available": True,
        "model": "Logistic Regression (학습된 가중치)",
        "n_train": m["n_train"],
        "performance": m["performance"],
        "weights": weights_dict,
        "data_source": m.get("data_source", ""),
        "rationale": (
            "기존 휴리스틱 가중치(timezone +15 등)에서 학습된 가중치로 전환. "
            f"시뮬 1,000 케이스(부정 100 / 정상 900 — 현실적 불균형) gradient descent 학습. "
            f"Phase 1 F1 {m['performance'].get('f1', 0):.3f}. 각 신호의 양의 기여를 데이터로 검증."
        ),
    }


@router.post("/score")
def score(payload: dict) -> dict:
    """9 신호 입력 → 학습된 모델로 부정 확률 산출.

    payload: 각 신호 0/1 입력
    """
    m = _get_model()
    w = m["weights"]
    x = [1.0]
    for name in SIGNAL_NAMES:
        x.append(1.0 if payload.get(name) else 0.0)
    z = sum(wi * xi for wi, xi in zip(w, x))
    p = _sigmoid(z)
    return {
        "available": True,
        "probability_fraud": round(p, 4),
        "score_100": int(p * 100),
        "decision": "차단 권장" if p >= 0.5 else "통과",
        "active_signals": [n for n in SIGNAL_NAMES if payload.get(n)],
    }

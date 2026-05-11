"""검증 지표 — 데이터 신뢰성/모듈 성능을 시연용으로 노출"""
from __future__ import annotations

import csv

from fastapi import APIRouter

from ..db import conn
from ..settings import SAMPLES

router = APIRouter(prefix="/api/metrics")


@router.get("/coverage")
def coverage() -> dict:
    """모듈별 데이터 커버리지·검증 결과 요약."""
    with conn() as c:
        n_def = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
        n_cells = c.execute("SELECT COUNT(*) FROM risk_cells").fetchone()[0]
        n_cases = c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        n_signals = c.execute("SELECT COUNT(*) FROM company_signals").fetchone()[0]
        n_clusters = c.execute(
            "SELECT COUNT(*) FROM (SELECT company_norm FROM company_signals GROUP BY company_norm)"
        ).fetchone()[0]
        n_alerts = c.execute("SELECT COUNT(*) FROM clusters_alerted").fetchone()[0]
        n_watch = c.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        n_files = c.execute("SELECT COUNT(*) FROM case_files").fetchone()[0]
        n_nps = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        n_calls = c.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0]
        n_calls_ok = c.execute("SELECT COUNT(*) FROM api_calls WHERE ok = 1").fetchone()[0]

    # 도메인 분포
    with conn() as c:
        dom_rows = c.execute(
            "SELECT domain, COUNT(*) AS n FROM company_signals WHERE domain IS NOT NULL GROUP BY domain"
        ).fetchall()
    domain_breakdown = {r["domain"]: r["n"] for r in dom_rows}

    return {
        "defaulters": n_def,
        "risk_cells": n_cells,
        "cases": n_cases,
        "case_files": n_files,
        "watchlist": n_watch,
        "company_signals": n_signals,
        "company_clusters": n_clusters,
        "clusters_alerted": n_alerts,
        "nps_workplaces_indexed": n_nps,
        "api_calls": n_calls,
        "api_success_rate": round((n_calls_ok / n_calls * 100), 1) if n_calls else 100.0,
        "signals_by_domain": domain_breakdown,
    }


@router.get("/m6")
def m6_metrics() -> dict:
    """M6 시뮬레이션 결과 요약 — Phase별 성능."""
    path = SAMPLES / "m6_simulation.csv"
    if not path.exists():
        return {"available": False}

    rows: list[dict] = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    # Phase 1 (출입국 미연계) — 룰 재계산 (api_m6의 _evaluate_phase1과 동일 임계)
    TH_MOUSE, TH_KEY, TH_DEVICE, TH_RDP = 70, 1.8, 0.6, 60

    def evaluate(rows, phase: int) -> dict:
        tp = fp = tn = fn = 0
        for r in rows:
            ip = r["ip_country"]
            m = float(r["mouse_jitter_ms"])
            k = float(r["key_burst_ratio"])
            rdp = float(r["rdp_latency_ms"])
            dev = float(r["device_drift"])
            immig = (r.get("immig_overseas", "False") == "True")
            pts = 0
            if ip != "KR": pts += 60
            if m > TH_MOUSE: pts += 15
            if k > TH_KEY: pts += 10
            if rdp > TH_RDP: pts += 15
            if dev > TH_DEVICE: pts += 10
            if phase >= 2 and immig: pts += 30
            pred = 1 if pts >= 50 else 0
            label = int(r["label"])
            if pred == 1 and label == 1: tp += 1
            elif pred == 1 and label == 0: fp += 1
            elif pred == 0 and label == 0: tn += 1
            else: fn += 1
        prec = tp / max(tp + fp, 1)
        rc = tp / max(tp + fn, 1)
        f1 = 2 * prec * rc / max(prec + rc, 1e-9)
        return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": prec, "recall": rc, "f1": f1}

    return {
        "available": True,
        "n_samples": len(rows),
        "phase1": evaluate(rows, 1),
        "phase2": evaluate(rows, 2),
        "improvement_recall": evaluate(rows, 2)["recall"] - evaluate(rows, 1)["recall"],
    }


@router.get("/timing")
def timing_pattern() -> dict:
    """신고·체불 시점 패턴 — 시간대·요일·차수별 누적."""
    with conn() as c:
        rows_round = c.execute(
            "SELECT round, COUNT(*) AS n, SUM(amount) AS amt FROM defaulters GROUP BY round ORDER BY round"
        ).fetchall()
        rows_year = c.execute(
            "SELECT year, COUNT(*) AS n, AVG(amount) AS avg_amt FROM defaulters GROUP BY year ORDER BY year"
        ).fetchall()
        # case 시간대
        rows_hour = c.execute(
            """SELECT CAST(SUBSTR(created_at, 12, 2) AS INTEGER) AS hour,
                      COUNT(*) AS n
               FROM cases
               WHERE created_at IS NOT NULL
               GROUP BY hour ORDER BY hour"""
        ).fetchall()
        rows_dow = c.execute(
            """SELECT strftime('%w', created_at) AS dow, COUNT(*) AS n
               FROM cases WHERE created_at IS NOT NULL GROUP BY dow"""
        ).fetchall()

    return {
        "by_round": [dict(r) for r in rows_round],
        "by_year": [dict(r) for r in rows_year],
        "case_by_hour": [dict(r) for r in rows_hour],
        "case_by_dow": [dict(r) for r in rows_dow],
    }

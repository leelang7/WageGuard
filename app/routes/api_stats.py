from __future__ import annotations

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api")


@router.get("/stats")
def stats() -> dict:
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
        amt = c.execute("SELECT COALESCE(SUM(amount),0) FROM defaulters").fetchone()[0]
        cells = c.execute("SELECT COUNT(*) FROM risk_cells").fetchone()[0]
        top = c.execute(
            "SELECT industry, region, risk_score FROM risk_cells ORDER BY risk_score DESC LIMIT 1"
        ).fetchone()
    return {
        "count": n,
        "amount": amt,
        "cells": cells,
        "top": dict(top) if top else {"industry": "-", "region": "-", "risk_score": 0},
    }


@router.get("/stats/by-industry")
def by_industry() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT industry, COUNT(*) as count, COALESCE(SUM(amount),0) as total
               FROM defaulters GROUP BY industry ORDER BY count DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/stats/by-region")
def by_region() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT region, COUNT(*) as count
               FROM defaulters WHERE region NOT IN ('(기타)','(미상)')
               GROUP BY region ORDER BY count DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/risk/cells")
def risk_cells(limit: int = 100) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT industry, region, risk_score, count, avg_amt, trend
               FROM risk_cells ORDER BY risk_score DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/industry/{name}")
def industry_detail(name: str) -> dict:
    with conn() as c:
        rows = c.execute(
            """SELECT round, name, age, company, region, amount
               FROM defaulters WHERE industry = ? ORDER BY amount DESC LIMIT 100""",
            (name,),
        ).fetchall()
        by_region = c.execute(
            """SELECT region, COUNT(*) as count FROM defaulters
               WHERE industry = ? AND region NOT IN ('(기타)','(미상)')
               GROUP BY region ORDER BY count DESC""",
            (name,),
        ).fetchall()
        by_round = c.execute(
            """SELECT round, COUNT(*) as count FROM defaulters
               WHERE industry = ? GROUP BY round ORDER BY round""",
            (name,),
        ).fetchall()
    return {
        "industry": name,
        "rows": [dict(r) for r in rows],
        "by_region": [dict(r) for r in by_region],
        "by_round": [dict(r) for r in by_round],
    }


@router.get("/home/stats")
def home_stats() -> dict:
    """홈 대시보드 전용 — SQL 집계만 사용, 빠른 응답."""
    with conn() as c:
        total_def = c.execute("SELECT COUNT(*) FROM defaulters").fetchone()[0]
        total_nps = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        nps_alert = c.execute(
            """SELECT COUNT(*) FROM nps_workplaces
               WHERE avg_pay > 0 AND avg_pay < 1800000 AND subscriber_cnt > 0
                 AND (CAST(lost_cnt AS REAL)/subscriber_cnt) >= 0.20"""
        ).fetchone()[0]
        dart_cnt = c.execute(
            "SELECT COUNT(*) FROM dart_financial_risks WHERE risk_score >= 20"
        ).fetchone()[0]
        cases_cnt = c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        high_approx = c.execute(
            "SELECT COUNT(*) FROM defaulters WHERE year >= 2025"
        ).fetchone()[0]
        med_approx = c.execute(
            "SELECT COUNT(*) FROM defaulters WHERE year = 2024"
        ).fetchone()[0]
        by_industry = c.execute(
            """SELECT industry, COUNT(*) as cnt FROM defaulters
               WHERE industry IS NOT NULL AND industry != ''
               GROUP BY industry ORDER BY cnt DESC LIMIT 10"""
        ).fetchall()
        by_region = c.execute(
            """SELECT region, COUNT(*) as cnt FROM defaulters
               WHERE region NOT IN ('(기타)','(미상)','')
               GROUP BY region ORDER BY cnt DESC LIMIT 16"""
        ).fetchall()
        by_year = c.execute(
            """SELECT year, COUNT(*) as cnt FROM defaulters
               WHERE year >= 2020 GROUP BY year ORDER BY year"""
        ).fetchall()
    return {
        "total_monitored": total_nps + total_def,
        "total_defaulters": total_def,
        "total_nps": total_nps,
        "nps_alert": nps_alert,
        "dart_risk": dart_cnt,
        "cases": cases_cnt,
        "high_approx": high_approx,
        "med_approx": med_approx,
        "by_industry": [{"industry": r["industry"], "cnt": r["cnt"]} for r in by_industry],
        "by_region":   [{"region": r["region"],   "cnt": r["cnt"]} for r in by_region],
        "by_year":     [{"year": r["year"],        "cnt": r["cnt"]} for r in by_year],
    }


@router.get("/region/{name}")
def region_detail(name: str) -> dict:
    with conn() as c:
        rows = c.execute(
            """SELECT round, name, company, industry, amount
               FROM defaulters WHERE region = ? ORDER BY amount DESC LIMIT 100""",
            (name,),
        ).fetchall()
        by_industry = c.execute(
            """SELECT industry, COUNT(*) as count FROM defaulters
               WHERE region = ? GROUP BY industry ORDER BY count DESC""",
            (name,),
        ).fetchall()
    return {
        "region": name,
        "rows": [dict(r) for r in rows],
        "by_industry": [dict(r) for r in by_industry],
    }

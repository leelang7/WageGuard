"""S5 감독관 우선순위 큐 — 지역별 점검 대상 자동 추천"""
from __future__ import annotations

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api/supervisor")


@router.get("/queue")
def queue(region: str | None = None, limit: int = 50) -> dict:
    """관할 지역의 점검 우선순위 큐.
    우선순위 기준:
      1. 셀 위험점수 × 사업장 체불액
      2. 동일 대표자 운영자 그룹은 묶어서 표시
      3. 차수 재등록 사례 우선
    """
    with conn() as c:
        # 셀 위험 큐
        if region:
            cells = c.execute(
                """SELECT industry, region, risk_score, count, avg_amt
                   FROM risk_cells WHERE region = ? ORDER BY risk_score DESC LIMIT ?""",
                (region, limit),
            ).fetchall()
            queue_rows = c.execute(
                """SELECT round, name, age, company, industry, region, owner_addr, amount,
                          (SELECT COUNT(*) FROM defaulters d2
                           WHERE d2.name=defaulters.name AND d2.age=defaulters.age) AS operator_size
                   FROM defaulters
                   WHERE region = ?
                   ORDER BY operator_size DESC, amount DESC LIMIT ?""",
                (region, limit),
            ).fetchall()
        else:
            cells = c.execute(
                "SELECT industry, region, risk_score, count, avg_amt FROM risk_cells ORDER BY risk_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
            queue_rows = c.execute(
                """SELECT round, name, age, company, industry, region, owner_addr, amount,
                          (SELECT COUNT(*) FROM defaulters d2
                           WHERE d2.name=defaulters.name AND d2.age=defaulters.age) AS operator_size
                   FROM defaulters
                   ORDER BY operator_size DESC, amount DESC LIMIT ?""",
                (limit,),
            ).fetchall()

        # 다중 운영자 (그래프 모듈과 동일 데이터, 큐 화면에서도 보이게)
        multi_ops = c.execute(
            """SELECT name, age, COUNT(*) as n, SUM(amount) as total
               FROM defaulters
               """ + ("WHERE region = ? " if region else "") + """
               GROUP BY name, age
               HAVING COUNT(*) > 1 OR COUNT(DISTINCT round) > 1
               ORDER BY total DESC""",
            (region,) if region else (),
        ).fetchall()

    return {
        "region": region,
        "cells": [dict(r) for r in cells],
        "queue": [dict(r) for r in queue_rows],
        "multi_operators": [dict(r) for r in multi_ops],
        "n_queue": len(queue_rows),
    }


@router.get("/regions")
def regions() -> list[str]:
    with conn() as c:
        rows = c.execute(
            """SELECT region FROM defaulters
               WHERE region NOT IN ('(기타)','(미상)')
               GROUP BY region ORDER BY COUNT(*) DESC"""
        ).fetchall()
    return [r["region"] for r in rows]

"""S1 동일 대표자 그래프 — 체불자 1명이 운영하는 모든 사업장 + 재등록 패턴"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import conn

router = APIRouter(prefix="/api/graph")


@router.get("/operators")
def operators() -> list[dict]:
    """다중 사업장 운영하는 체불자 명단."""
    with conn() as c:
        rows = c.execute(
            """SELECT name, age,
                      COUNT(*) AS n_companies,
                      COUNT(DISTINCT round) AS n_rounds,
                      SUM(amount) AS total_amount,
                      GROUP_CONCAT(DISTINCT industry) AS industries,
                      MIN(round) AS first_round, MAX(round) AS last_round
               FROM defaulters
               GROUP BY name, age
               HAVING COUNT(*) > 1 OR COUNT(DISTINCT round) > 1
               ORDER BY total_amount DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/person/{name}/{age}")
def person(name: str, age: int) -> dict:
    """특정 인물(성명 + 나이)이 운영한 모든 사업장 + 시계열 + 그래프 노드."""
    with conn() as c:
        rows = c.execute(
            """SELECT round, year, company, industry, region,
                      owner_addr, company_addr, amount
               FROM defaulters
               WHERE name = ? AND age = ?
               ORDER BY round""",
            (name, age),
        ).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        raise HTTPException(404, f"체불 명단에 {name}({age}세) 없음")

    # 그래프 노드/엣지 (ECharts graph series)
    person_id = f"P:{name}:{age}"
    nodes = [
        {
            "id": person_id, "name": f"{name} ({age}세)",
            "category": 0, "symbolSize": 60, "value": sum(r["amount"] for r in rows),
        }
    ]
    edges = []
    for r in rows:
        cid = f"C:{r['company']}"
        nodes.append(
            {
                "id": cid, "name": r["company"],
                "category": 1,
                "symbolSize": min(80, 20 + r["amount"] / 10_000_000),
                "value": r["amount"],
                "info": f"{r['round']} · {r['industry']} · {r['region']} · {r['amount']:,}원",
            }
        )
        edges.append(
            {"source": person_id, "target": cid, "value": r["amount"], "label": {"show": True, "formatter": r["round"]}}
        )

    timeline = []
    for r in rows:
        timeline.append(
            {"round": r["round"], "company": r["company"], "amount": r["amount"], "industry": r["industry"]}
        )

    return {
        "person": {"name": name, "age": age},
        "rows": rows,
        "total_amount": sum(r["amount"] for r in rows),
        "n_companies": len({r["company"] for r in rows}),
        "n_rounds": len({r["round"] for r in rows}),
        "timeline": timeline,
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "categories": [{"name": "체불자"}, {"name": "사업장"}],
        },
    }


@router.get("/paper-companies")
def paper_companies() -> dict:
    """페이퍼컴퍼니 의심 클러스터 — HMRC Connect 벤치마킹.

    탐지 룰:
      1) 동일 (성명, 나이) 다중 사업장 (S1 기존)
      2) 동일 주소지(owner_addr 정규화)에서 시간 인접 등록
      3) 다른 차수에 동일 인물·다른 사업장 (재창업 패턴)
      4) 동일 사업장명에 다른 대표자 — 명의 도용 가능성
    """
    import re

    def addr_key(s: str) -> str:
        if not s:
            return ""
        # 시·구·동 단위까지만 (도로명/지번 세부 제거)
        s = re.sub(r"\s+\d.*$", "", s)
        return s.strip()

    out: list[dict] = []
    with conn() as c:
        # 1) 다중 사업장 운영자
        ops = c.execute(
            """SELECT name, age, owner_addr, COUNT(*) AS n,
                      GROUP_CONCAT(company, '|') AS companies,
                      GROUP_CONCAT(round, '|') AS rounds
               FROM defaulters
               GROUP BY name, age
               HAVING COUNT(*) >= 2 OR COUNT(DISTINCT round) >= 2"""
        ).fetchall()
        for r in ops:
            r = dict(r)
            r["pattern"] = "동일 인물 다중 사업장"
            r["addr_key"] = addr_key(r["owner_addr"])
            r["risk"] = min(100, r["n"] * 25 + (10 if "|" in (r["rounds"] or "") else 0))
            out.append(r)

        # 2) 동일 주소지 클러스터 (다른 인물도 포함)
        addr_groups = c.execute(
            """SELECT owner_addr, COUNT(DISTINCT (name || '|' || age)) AS n_persons,
                      COUNT(*) AS n_companies,
                      GROUP_CONCAT(DISTINCT name) AS names,
                      GROUP_CONCAT(company, '|') AS companies
               FROM defaulters
               WHERE owner_addr IS NOT NULL AND owner_addr != ''
               GROUP BY owner_addr
               HAVING n_companies >= 2"""
        ).fetchall()
        for r in addr_groups:
            r = dict(r)
            if r["n_persons"] < 2:
                continue
            r["pattern"] = "동일 주소지 다중 인물 (페이퍼컴퍼니 의심)"
            r["risk"] = min(100, r["n_companies"] * 20 + r["n_persons"] * 15)
            out.append({
                "name": "(다인)", "age": 0,
                "owner_addr": r["owner_addr"],
                "addr_key": addr_key(r["owner_addr"]),
                "n": r["n_companies"],
                "companies": r["companies"],
                "rounds": "",
                "pattern": r["pattern"],
                "risk": r["risk"],
                "n_persons": r["n_persons"],
                "names": r["names"],
            })

    out.sort(key=lambda r: -r["risk"])
    return {
        "candidates": out[:50],
        "disclaimer": (
            "동일 주소지가 곧 페이퍼컴퍼니는 아닙니다. 영세 빌딩·임대 사무실 다중 입주 false positive 가능. "
            "감독관이 추가 검증 (대표자 동일성·세무·재무) 후 판정 필요."
        ),
        "method": "rule_v1: (성명·나이) 다중 사업장 + 동일 owner_addr 다중 인물",
    }


@router.get("/paper-companies-list")
def paper_companies_list() -> list[dict]:
    """기존 호환 — 캔디데이트 리스트만."""
    full = paper_companies()
    return full["candidates"]


@router.get("/network")
def network(min_companies: int = 2) -> dict:
    """전체 다중 운영자 네트워크 — 한 화면에 한국 체불 그래프."""
    with conn() as c:
        ops = c.execute(
            """SELECT name, age FROM defaulters
               GROUP BY name, age HAVING COUNT(*) > 1 OR COUNT(DISTINCT round) > 1"""
        ).fetchall()

        nodes = []
        edges = []
        seen = set()
        for op in ops:
            pid = f"P:{op['name']}:{op['age']}"
            rows = c.execute(
                "SELECT round, company, industry, amount FROM defaulters WHERE name=? AND age=?",
                (op["name"], op["age"]),
            ).fetchall()
            total = sum(r["amount"] for r in rows)

            nodes.append(
                {
                    "id": pid, "name": f"{op['name']}({op['age']})",
                    "category": 0, "symbolSize": min(60, 20 + total / 50_000_000),
                    "value": total,
                }
            )
            for r in rows:
                cid = f"C:{r['company']}:{op['name']}:{op['age']}"
                if cid in seen:
                    continue
                seen.add(cid)
                nodes.append(
                    {
                        "id": cid, "name": r["company"],
                        "category": 1,
                        "symbolSize": min(40, 12 + r["amount"] / 20_000_000),
                        "value": r["amount"],
                        "info": f"{r['round']} · {r['industry']} · {r['amount']:,}원",
                    }
                )
                edges.append({"source": pid, "target": cid, "value": r["amount"]})

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": [{"name": "체불자"}, {"name": "사업장"}],
        "n_operators": len(ops),
    }

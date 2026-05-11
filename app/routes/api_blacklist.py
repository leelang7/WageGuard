"""나라장터 부정당업자 명단 — 조달청 입찰 제한 사업주.

근로기준법·임금체불 등으로 입찰 제한된 사업주는 정직성 negative 신호.
data.go.kr `조달청_부정당업자 정보`. CSV 또는 OpenAPI 형태로 제공.

본 모듈은 ① data.go.kr OpenAPI 시도 → 실패 시 ② 사용자가 samples/에
다운받아 둔 부정당업자 CSV를 SQLite로 색인.
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

import requests
from fastapi import APIRouter

from ..db import conn, init_db
from ..settings import SAMPLES
from .api_business import log_call
from .api_cluster import add_signal, normalize as normalize_company

router = APIRouter(prefix="/api/blacklist")

API_URL = "http://apis.data.go.kr/1230000/UnscrupulousBidderService/getUnscrupulousBidderList"


def init_table() -> None:
    with conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT, company_norm TEXT,
                bzowr_rgst_no TEXT,
                representative TEXT,
                reason TEXT,
                ban_from TEXT, ban_to TEXT,
                source TEXT,
                created_at TEXT
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_blk_norm ON blacklist(company_norm)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_blk_bno  ON blacklist(bzowr_rgst_no)")


def ingest_csv() -> int:
    """samples/에 다운받아 둔 '부정당업자' CSV 자동 색인."""
    init_table()
    candidates = list(SAMPLES.glob("*부정당*.csv")) + list(SAMPLES.glob("*UnscrupulousBidder*.csv"))
    if not candidates:
        return 0
    path = candidates[0]
    n = 0
    with conn() as c:
        c.execute("DELETE FROM blacklist")
        for enc in ("cp949", "utf-8-sig", "utf-8"):
            try:
                f = path.open(encoding=enc, newline="")
                reader = csv.DictReader(f)
                for row in reader:
                    name = (row.get("업체명") or row.get("상호") or row.get("기업명") or "").strip()
                    if not name:
                        continue
                    bno = (row.get("사업자등록번호") or row.get("사업자번호") or "").replace("-", "").strip()
                    rep = (row.get("대표자") or row.get("대표") or "").strip()
                    reason = (row.get("제재사유") or row.get("사유") or "").strip()
                    bf = (row.get("제재시작일") or row.get("제재 시작일") or "").strip()
                    bt = (row.get("제재종료일") or row.get("제재 종료일") or "").strip()
                    c.execute(
                        """INSERT INTO blacklist (company, company_norm, bzowr_rgst_no,
                           representative, reason, ban_from, ban_to, source, created_at)
                           VALUES (?,?,?,?,?,?,?,?, datetime('now'))""",
                        (name, normalize_company(name), bno, rep, reason, bf, bt, path.name),
                    )
                    n += 1
                f.close()
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
    return n


@router.post("/ingest")
def trigger_ingest() -> dict:
    n = ingest_csv()
    return {"ingested": n}


@router.get("/search")
def search(q: str) -> dict:
    init_table()
    if not q.strip():
        return {"matched": 0, "items": []}
    is_bno = q.isdigit() and len(q) == 10
    norm = normalize_company(q)
    with conn() as c:
        if is_bno:
            rows = c.execute("SELECT * FROM blacklist WHERE bzowr_rgst_no = ? LIMIT 20", (q,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM blacklist WHERE company_norm LIKE ? OR company LIKE ? LIMIT 20",
                (f"%{norm}%", f"%{q}%"),
            ).fetchall()

    items = [dict(r) for r in rows]
    if items:
        # 부정당업자 등재 = 강한 정직성 부정 신호 → reputation + pay_default 도메인 동시
        add_signal(
            company_raw=q, channel="external", domain="reputation",
            severity="high",
            source_ref=f"부정당업자 등재 {len(items)}건",
        )
        # 사유에 "임금체불·근로기준법" 포함이면 pay_default도
        if any("임금" in (r.get("reason") or "") or "근로기준" in (r.get("reason") or "") for r in items):
            add_signal(
                company_raw=q, channel="external", domain="pay_default",
                severity="high",
                source_ref="부정당업자 사유: 임금/근로기준법",
            )
    return {
        "query": q,
        "matched": len(items),
        "items": items,
        "note": "부정당업자 등재는 입찰 제한 = 정부와의 거래 자격 박탈. 정직성 부정 신호.",
    }


@router.get("/all")
def all_blacklist(limit: int = 100) -> dict:
    init_table()
    with conn() as c:
        rows = c.execute("SELECT * FROM blacklist ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        n_total = c.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]
    return {
        "total": n_total,
        "items": [dict(r) for r in rows],
    }

"""DART 재무데이터 — 체불 선행지표 (부채비율·영업손실·자본잠식·유동비율)

한계: 상장사 + 사업보고서 제출 비상장사만 커버 (영세 사업장 미수록).
      그러나 중견 이상 규모 체불 사건의 상당수가 이 범위에 해당.

체불 선행지표 근거:
- 부채비율 300%↑ : 유동성 위기 전조 (Altman Z-score 구성 요소)
- 영업이익 < 0   : 본업 손실 지속 → 인건비가 먼저 지급 불능화
- 자본잠식       : 이미 재무 붕괴 상태
- 유동비율 100%↓ : 단기 지급 능력 상실
"""
from __future__ import annotations

import os
import time
import json
import zipfile
from io import BytesIO
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import requests
from fastapi import APIRouter

from .api_business import log_call
from .api_cluster import add_signal
from ..db import conn
from ..settings import DATA_DIR

router = APIRouter(prefix="/api/dart")

DART_BASE = "https://opendart.fss.or.kr/api"
CORP_CACHE = DATA_DIR / "dart_corp_codes.json"


def _key() -> str:
    return os.environ.get("OPENDART_KEY", "").strip()


def _get(endpoint: str, params: dict) -> tuple[dict | None, int, int]:
    key = _key()
    if not key:
        return None, 0, 0
    t0 = time.time()
    try:
        r = requests.get(
            f"{DART_BASE}/{endpoint}",
            params={"crtfc_key": key, **params},
            timeout=15,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("DART", endpoint, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, r.status_code, dt
        return r.json(), r.status_code, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call("DART", endpoint, 0, dt, False)
        return None, 0, dt


def _corp_cls(stock_code: str) -> str:
    """DART corpCode에는 시장 구분이 없어 상장 여부만 보수적으로 표시."""
    return "Y" if stock_code else "N"


def _load_corp_codes(force_refresh: bool = False) -> list[dict]:
    """OpenDART 기업코드 ZIP(corpCode.xml)을 받아 로컬 캐시에 저장."""
    if CORP_CACHE.exists() and not force_refresh:
        try:
            age_sec = time.time() - CORP_CACHE.stat().st_mtime
            if age_sec < 86400 * 7:
                return json.loads(CORP_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    key = _key()
    if not key:
        return []

    t0 = time.time()
    try:
        r = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": key},
            timeout=30,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("DART", "corpCode.xml", r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return []

        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), "")
            if not xml_name:
                return []
            root = ET.fromstring(zf.read(xml_name))

        rows = []
        for item in root.findall("list"):
            corp_name = (item.findtext("corp_name") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            modify_date = (item.findtext("modify_date") or "").strip()
            if corp_name and corp_code:
                rows.append({
                    "corp_code": corp_code,
                    "corp_name": corp_name,
                    "stock_code": stock_code,
                    "corp_cls": _corp_cls(stock_code),
                    "modify_date": modify_date,
                })
        CORP_CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows
    except Exception:
        return []


def _norm_name(name: str) -> str:
    import re as _re
    return _re.sub(r"[\s\(\)（）\[\]【】·,\.\-_/]", "", name or "").lower()


def _persist_corp_codes(rows: list[dict]) -> int:
    """DART 기업코드 목록을 SQLite 검색 유니버스로 적재."""
    if not rows:
        return 0
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with conn() as c:
        c.executemany(
            """INSERT INTO dart_corps
               (corp_code, corp_name, corp_name_norm, stock_code, corp_cls, modify_date, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(corp_code) DO UPDATE SET
                 corp_name=excluded.corp_name,
                 corp_name_norm=excluded.corp_name_norm,
                 stock_code=excluded.stock_code,
                 corp_cls=excluded.corp_cls,
                 modify_date=excluded.modify_date,
                 updated_at=excluded.updated_at""",
            [
                (
                    r.get("corp_code"),
                    r.get("corp_name"),
                    _norm_name(r.get("corp_name", "")),
                    r.get("stock_code") or "",
                    r.get("corp_cls") or _corp_cls(r.get("stock_code", "")),
                    r.get("modify_date") or "",
                    now,
                )
                for r in rows
            ],
        )
    return len(rows)


def search_corp(name: str) -> list[dict]:
    """회사명으로 DART 기업코드 검색.

    OpenDART는 회사명 검색 API가 따로 없어서 corpCode.xml 전체 목록을 캐시한 뒤
    로컬에서 완전일치/부분일치 순으로 검색한다.
    """
    q = (name or "").strip()
    q_norm = _norm_name(q)
    if not q_norm:
        return []

    with conn() as c:
        exact = c.execute(
            "SELECT corp_code, corp_name, stock_code, corp_cls, modify_date "
            "FROM dart_corps WHERE corp_name_norm = ? "
            "ORDER BY CASE WHEN stock_code != '' THEN 0 ELSE 1 END, corp_name LIMIT 5",
            (q_norm,),
        ).fetchall()
        if exact:
            return [dict(r) for r in exact]
        partial = c.execute(
            "SELECT corp_code, corp_name, stock_code, corp_cls, modify_date "
            "FROM dart_corps WHERE corp_name_norm LIKE ? "
            "ORDER BY CASE WHEN stock_code != '' THEN 0 ELSE 1 END, corp_name LIMIT 5",
            (f"%{q_norm}%",),
        ).fetchall()
        if partial:
            return [dict(r) for r in partial]

    rows = _load_corp_codes()
    _persist_corp_codes(rows)
    exact = []
    partial = []
    for row in rows:
        nm = row["corp_name"]
        nm_norm = _norm_name(nm)
        if nm_norm == q_norm:
            exact.append(row)
        elif q_norm in nm_norm:
            partial.append(row)

    def rank(row: dict) -> tuple[int, str]:
        listed = 0 if row.get("stock_code") else 1
        return (listed, row.get("corp_name", ""))

    return sorted(exact, key=rank)[:5] or sorted(partial, key=rank)[:5]


def _fetch_accounts(corp_code: str, year: int) -> list[dict]:
    data, _, _ = _get(
        "fnlttSinglAcnt.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if data and data.get("status") == "000":
        return data.get("list") or []
    return []


def _fetch_ratio(corp_code: str, year: int) -> dict:
    """재무비율 API — 부채비율·유동비율·ROE 직접 수록."""
    data, _, _ = _get(
        "fnlttFinaRatio.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    if not data or data.get("status") != "000":
        return {}
    items = data.get("list") or []
    out: dict = {}
    for it in items:
        nm = (it.get("account_nm") or "").strip()
        val_str = (it.get("thstrm_amount") or "").replace(",", "").replace("%", "").strip()
        try:
            val = float(val_str)
        except ValueError:
            continue
        if "부채비율" in nm:
            out["debt_ratio"] = val
        elif "유동비율" in nm:
            out["current_ratio"] = val
        elif "자기자본비율" in nm:
            out["equity_ratio"] = val
        elif "영업이익률" in nm:
            out["op_margin"] = val
        elif "ROE" in nm or "자기자본이익률" in nm:
            out["roe"] = val
    return out


def _pick(accounts: list[dict], labels: list[str], fs_div: str = "CFS") -> int | None:
    for label in labels:
        for acc in accounts:
            if acc.get("fs_div") == fs_div and label in (acc.get("account_nm") or ""):
                try:
                    return int((acc.get("thstrm_amount") or "0").replace(",", ""))
                except ValueError:
                    continue
    if fs_div == "CFS":
        return _pick(accounts, labels, "OFS")
    return None


def compute_risk(accounts: list[dict], ratios: dict, year: int) -> dict:
    """재무 신호 → 체불 위험도 산출."""
    assets      = _pick(accounts, ["자산총계"])
    liabilities = _pick(accounts, ["부채총계"])
    equity      = _pick(accounts, ["자본총계"])
    op_income   = _pick(accounts, ["영업이익", "영업손익"])
    cur_assets  = _pick(accounts, ["유동자산"])
    cur_liab    = _pick(accounts, ["유동부채"])
    net_income  = _pick(accounts, ["당기순이익", "당기순손익"])

    # 비율: API 직접값 우선, 없으면 계산
    debt_ratio    = ratios.get("debt_ratio")
    current_ratio = ratios.get("current_ratio")
    if debt_ratio is None and liabilities and equity and equity > 0:
        debt_ratio = round(liabilities / equity * 100, 1)
    if current_ratio is None and cur_assets and cur_liab and cur_liab > 0:
        current_ratio = round(cur_assets / cur_liab * 100, 1)

    score = 0
    signals: list[dict] = []

    if equity is not None and equity < 0:
        score += 40
        signals.append({"label": "완전 자본잠식 (자본총계 음수)", "pts": 40, "severity": "critical"})
    elif equity and assets and equity > 0 and equity / assets < 0.05:
        score += 20
        signals.append({"label": f"자본잠식 임박 (자기자본비율 {equity/assets*100:.1f}%)", "pts": 20, "severity": "high"})

    if debt_ratio is not None:
        if debt_ratio > 500:
            score += 35
            signals.append({"label": f"부채비율 {debt_ratio:.0f}% (500% 초과)", "pts": 35, "severity": "critical"})
        elif debt_ratio > 300:
            score += 25
            signals.append({"label": f"부채비율 {debt_ratio:.0f}% (300% 초과)", "pts": 25, "severity": "high"})
        elif debt_ratio > 200:
            score += 10
            signals.append({"label": f"부채비율 {debt_ratio:.0f}%", "pts": 10, "severity": "medium"})

    if op_income is not None and op_income < 0:
        loss_bn = round(abs(op_income) / 1e8, 1)
        pts = 30 if loss_bn > 100 else (20 if loss_bn > 10 else 10)
        score += pts
        signals.append({"label": f"영업손실 {loss_bn:.1f}억원", "pts": pts, "severity": "high"})

    if current_ratio is not None:
        if current_ratio < 50:
            score += 20
            signals.append({"label": f"유동비율 {current_ratio:.0f}% (단기 지급불능 위험)", "pts": 20, "severity": "critical"})
        elif current_ratio < 100:
            score += 10
            signals.append({"label": f"유동비율 {current_ratio:.0f}% (100% 미만)", "pts": 10, "severity": "high"})

    return {
        "year": year,
        "risk_score": min(score, 100),
        "signals": signals,
        "financials": {
            "assets": assets, "liabilities": liabilities, "equity": equity,
            "op_income": op_income, "net_income": net_income,
            "debt_ratio": debt_ratio, "current_ratio": current_ratio,
        },
    }


# ── 엔드포인트 ──────────────────────────────────────────────────────

@router.get("/search")
def corp_search(company: str) -> dict:
    """회사명 → DART 기업코드 검색."""
    if not _key():
        return {"available": False, "reason": "OPENDART_KEY 미설정"}
    results = search_corp(company.strip())
    return {"available": True, "query": company, "results": results}


@router.get("/risk/{corp_code}")
def financial_risk(corp_code: str, year: int | None = None) -> dict:
    """기업코드 → 재무 위험도 산출."""
    if not _key():
        return {"available": False, "reason": "OPENDART_KEY 미설정"}
    y = year or datetime.now().year - 1
    accounts = _fetch_accounts(corp_code, y)
    if not accounts:
        accounts = _fetch_accounts(corp_code, y - 1)
        if accounts:
            y -= 1
    if not accounts:
        return {"available": False, "reason": f"{corp_code} 재무데이터 없음 (비상장 소규모 등)", "corp_code": corp_code}
    ratios = _fetch_ratio(corp_code, y)
    result = compute_risk(accounts, ratios, y)
    return {"available": True, "corp_code": corp_code, **result}


def _save_financial_risk(corp: dict, risk: dict, source: str) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with conn() as c:
        c.execute(
            """INSERT INTO dart_financial_risks
               (corp_code, corp_name, stock_code, year, risk_score, signals, financials, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(corp_code) DO UPDATE SET
                 corp_name=excluded.corp_name,
                 stock_code=excluded.stock_code,
                 year=excluded.year,
                 risk_score=excluded.risk_score,
                 signals=excluded.signals,
                 financials=excluded.financials,
                 source=excluded.source,
                 fetched_at=excluded.fetched_at""",
            (
                corp.get("corp_code"),
                corp.get("corp_name"),
                corp.get("stock_code") or "",
                risk.get("year"),
                risk.get("risk_score"),
                json.dumps(risk.get("signals") or [], ensure_ascii=False),
                json.dumps(risk.get("financials") or {}, ensure_ascii=False),
                source,
                now,
            ),
        )


@router.get("/diagnose")
def diagnose(company: str, add_to_cluster: bool = True) -> dict:
    """회사명 → 코드 검색 → 재무 위험도 통합 진단.

    체불 선행지표 4종 (부채비율·영업손실·자본잠식·유동비율) 자동 산출.
    """
    if not _key():
        return {"available": False, "reason": "OPENDART_KEY 미설정 — 실제 DART API 데이터만 표시합니다."}

    corps = search_corp(company.strip())
    if not corps:
        return {
            "available": False,
            "reason": "DART에서 해당 회사 미발견 (영세 사업장·개인사업자 미수록 가능)",
            "query": company,
        }

    corp = corps[0]
    corp_code = corp["corp_code"]
    y = datetime.now().year - 1
    accounts = _fetch_accounts(corp_code, y)
    if not accounts:
        accounts = _fetch_accounts(corp_code, y - 1)
        if accounts:
            y -= 1

    if not accounts:
        return {
            "available": False,
            "reason": "재무제표 데이터 없음 (사업보고서 미제출 등)",
            "corp": corp,
        }

    ratios = _fetch_ratio(corp_code, y)
    risk = compute_risk(accounts, ratios, y)
    _save_financial_risk(corp, risk, "diagnose")

    if add_to_cluster and risk["risk_score"] >= 30:
        sev = "critical" if risk["risk_score"] >= 60 else "high"
        add_signal(
            company_raw=company.strip(),
            channel="dart", domain="finance",
            severity=sev,
            source_ref=f"DART:{corp_code}:부채{risk['financials'].get('debt_ratio','?')}%",
        )

    return {
        "available": True,
        "query": company,
        "corp": corp,
        **risk,
        "note": "DART = 상장사·사업보고서 제출 비상장사 커버. 소규모 영세 사업장 미수록.",
        "source": "금융감독원 전자공시시스템 (DART) — opendart.fss.or.kr",
    }


@router.get("/catalog")
def catalog() -> dict:
    """DART API 사용 가능 여부 및 커버리지 설명."""
    with conn() as c:
        corp_count = c.execute("SELECT COUNT(*) AS n FROM dart_corps").fetchone()["n"]
        listed_count = c.execute("SELECT COUNT(*) AS n FROM dart_corps WHERE stock_code != ''").fetchone()["n"]
        risk_count = c.execute("SELECT COUNT(*) AS n FROM dart_financial_risks").fetchone()["n"]
    return {
        "available": bool(_key()),
        "key_env": "OPENDART_KEY",
        "corp_universe_count": corp_count,
        "listed_universe_count": listed_count,
        "financial_risk_cached": risk_count,
        "coverage": "상장사 + 사업보고서 제출 비상장사 (영세 개인사업자 제외)",
        "endpoints_used": [
            "corpCode.xml — 전체 기업코드 ZIP 수신 후 로컬 회사명 검색",
            "fnlttSinglAcnt.json — 단일회사 주요계정 (사업보고서)",
            "재무비율 — 주요계정에서 부채비율·유동비율 직접 계산",
        ],
        "risk_signals": [
            "부채비율 > 300% → 체불 고위험 (Altman Z-score 구성요소)",
            "영업이익 < 0 → 본업 손실 지속",
            "자본총계 < 0 → 완전 자본잠식",
            "유동비율 < 100% → 단기 지급 불능",
        ],
        "issue_url": "https://opendart.fss.or.kr/intro/main.do",
    }


@router.post("/ingest-corps")
def ingest_corps(force_refresh: bool = False) -> dict:
    """OpenDART 전체 기업코드 목록을 DB에 적재."""
    rows = _load_corp_codes(force_refresh=force_refresh)
    n = _persist_corp_codes(rows)
    listed = sum(1 for r in rows if r.get("stock_code"))
    return {
        "available": bool(rows),
        "inserted_or_updated": n,
        "listed_count": listed,
        "cache_file": str(CORP_CACHE),
    }


@router.get("/universe")
def universe(q: str | None = None, limit: int = 20, listed_only: bool = False) -> dict:
    """DART 분석 대상 기업 유니버스 조회."""
    limit = max(1, min(limit, 100))
    where = []
    args: list[Any] = []
    if q:
        where.append("corp_name_norm LIKE ?")
        args.append(f"%{_norm_name(q)}%")
    if listed_only:
        where.append("stock_code != ''")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    with conn() as c:
        total = c.execute(f"SELECT COUNT(*) AS n FROM dart_corps{sql_where}", args).fetchone()["n"]
        rows = c.execute(
            f"""SELECT corp_code, corp_name, stock_code, corp_cls, modify_date
                FROM dart_corps{sql_where}
                ORDER BY CASE WHEN stock_code != '' THEN 0 ELSE 1 END, corp_name
                LIMIT ?""",
            [*args, limit],
        ).fetchall()
    return {"total": total, "results": [dict(r) for r in rows]}


@router.get("/risk-cache")
def risk_cache(limit: int = 50) -> dict:
    """이미 실제 조회한 DART 재무위험 캐시."""
    limit = max(1, min(limit, 200))
    with conn() as c:
        rows = c.execute(
            """SELECT corp_code, corp_name, stock_code, year, risk_score, signals,
                      financials, source, fetched_at
               FROM dart_financial_risks
               ORDER BY risk_score DESC, fetched_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["signals"] = json.loads(item["signals"] or "[]")
            item["financials"] = json.loads(item["financials"] or "{}")
        except Exception:
            pass
        out.append(item)
    with conn() as c:
        total = c.execute("SELECT COUNT(*) as n FROM dart_financial_risks").fetchone()["n"]
    return {"count": total, "results": out}


@router.post("/batch-risk")
def batch_risk(limit: int = 30, min_stock_code: str = "", year: int | None = None) -> dict:
    """상장사 DART 재무위험을 실제 API로 배치 적재.

    과도한 호출을 피하려고 기본 30개만 처리한다. 이미 캐시된 기업은 건너뛴다.
    """
    limit = max(1, min(limit, 200))
    with conn() as c:
        rows = c.execute(
            """SELECT dc.corp_code, dc.corp_name, dc.stock_code, dc.corp_cls, dc.modify_date
               FROM dart_corps dc
               LEFT JOIN dart_financial_risks fr ON fr.corp_code = dc.corp_code
               WHERE dc.stock_code != ''
                 AND dc.stock_code >= ?
                 AND fr.corp_code IS NULL
               ORDER BY dc.stock_code
               LIMIT ?""",
            (min_stock_code, limit),
        ).fetchall()

    y_default = year or datetime.now().year - 1
    processed = []
    failed = []
    for row in rows:
        corp = dict(row)
        y = y_default
        accounts = _fetch_accounts(corp["corp_code"], y)
        if not accounts:
            accounts = _fetch_accounts(corp["corp_code"], y - 1)
            if accounts:
                y -= 1
        if not accounts:
            failed.append({"corp_code": corp["corp_code"], "corp_name": corp["corp_name"], "reason": "재무제표 없음"})
            continue
        ratios = _fetch_ratio(corp["corp_code"], y)
        risk = compute_risk(accounts, ratios, y)
        _save_financial_risk(corp, risk, "batch-risk")
        processed.append({
            "corp_code": corp["corp_code"],
            "corp_name": corp["corp_name"],
            "stock_code": corp["stock_code"],
            "year": risk["year"],
            "risk_score": risk["risk_score"],
            "signals": risk["signals"],
        })

    return {
        "requested": limit,
        "processed": len(processed),
        "failed": len(failed),
        "results": processed,
        "failed_sample": failed[:10],
    }

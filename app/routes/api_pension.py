"""국민연금 가입·탈퇴 사업장 정보 — 사업장 단위 권위 데이터.

가입(15020284) → 가입자수·취득/상실·평균보수
탈퇴(15020284) → 최근 폐업·탈퇴 이력

이게 진짜 사업장 신호의 코어. 회전율(상실/취득)과 가입자 급감이 강력한
사전 징후. 사업장명이나 사업자번호로 직접 검색 가능.
"""
from __future__ import annotations

import os
import time

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from .api_business import log_call
from .api_cluster import add_signal
from ..db import conn

router = APIRouter(prefix="/api/pension")

NPS_BASE = "http://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2"
ENROLLED = f"{NPS_BASE}/getBassInfoSearchV2"
WITHDRAWN = "http://apis.data.go.kr/B552015/NpsScsnBplcInfoInqireServiceV2/getBassInfoSearchV2"


def _call_nps(endpoint: str, params: dict) -> tuple[dict | None, int, int]:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        return None, 0, 0
    t0 = time.time()
    try:
        r = requests.get(
            endpoint,
            params={"serviceKey": key, "_type": "json", "numOfRows": 20, **params},
            timeout=15,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("NPS", endpoint, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, r.status_code, dt
        return r.json(), r.status_code, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call("NPS", endpoint, 0, dt, False)
        return None, 0, dt


def _items_from(payload: dict) -> list[dict]:
    body = (payload or {}).get("response", {}).get("body", {})
    items = body.get("items", {})
    if isinstance(items, dict):
        item = items.get("item", [])
        if isinstance(item, dict):
            return [item]
        return item or []
    return []


def search_enrolled(name_or_bno: str) -> tuple[list[dict], int, int]:
    is_bno = name_or_bno.isdigit() and len(name_or_bno) == 10
    params = {"bzowrRgstNo": name_or_bno} if is_bno else {"wkpl_nm": name_or_bno}
    data, status, dt = _call_nps(ENROLLED, params)
    return _items_from(data or {}), status, dt


def signals_from_enrollment(items: list[dict]) -> dict:
    if not items:
        return {"available": True, "matched": 0, "signals": []}

    signals: list[dict] = []
    severities: list[str] = []

    summary = []
    for it in items[:10]:
        nm = it.get("wkplNm", "")
        bno = it.get("bzowrRgstNo", "")
        join = int((it.get("jnngpCnt") or it.get("subscbrCnt") or 0) or 0)
        new_  = int((it.get("newSubscbrCnt") or it.get("jnngpAcqsCnt") or 0) or 0)
        lost  = int((it.get("lossSubscbrCnt") or it.get("jnngpFrftCnt") or 0) or 0)
        avg_pay = int((it.get("avrgPay") or it.get("notiAmt") or 0) or 0)
        addr = it.get("ldongAddrMgplDgCdNm", "") or it.get("ldong_addr_mgpl_dg_cd_nm", "") or ""
        regdt = it.get("adptDt") or it.get("crrmmInsmntCrldte") or ""

        ws = []
        # 신호 1: 회전율 — 상실/(취득+상실)
        churn_basis = (new_ or 0) + (lost or 0)
        churn = (lost / churn_basis) if churn_basis else None
        # 신호 2: 가입자수 대비 상실 비율
        loss_ratio = (lost / join) if join else None
        # 신호 3: 평균 보수 (저임금 사업장 여부)
        # 2024 최저시급 9860원 → 월 ~206만원 (40h주). 그 미만은 단시간/위험 가능성

        if churn is not None and churn >= 0.5 and (lost or 0) >= 3:
            ws.append({"sig": f"회전율 {round(churn*100)}% (상실 {lost}/취득 {new_})", "severity": "high"})
            severities.append("high")
        elif churn is not None and churn >= 0.35 and (lost or 0) >= 2:
            ws.append({"sig": f"회전율 {round(churn*100)}% (상실 {lost}/취득 {new_})", "severity": "medium"})
            severities.append("medium")

        if loss_ratio is not None and loss_ratio >= 0.20 and (lost or 0) >= 3:
            ws.append({"sig": f"월 상실률 {round(loss_ratio*100)}% (가입 {join} 중 {lost} 이탈)", "severity": "high"})
            severities.append("high")

        if avg_pay and avg_pay < 1500000:
            ws.append({"sig": f"평균 보수월액 {avg_pay:,}원 — 저임금 사업장", "severity": "medium"})
            severities.append("medium")

        summary.append({
            "wkplNm": nm,
            "bzowrRgstNo": bno,
            "jnngpCnt": join,
            "new": new_,
            "lost": lost,
            "churn_pct": int(round(churn * 100)) if churn is not None else None,
            "loss_pct": int(round(loss_ratio * 100)) if loss_ratio is not None else None,
            "avg_pay": avg_pay,
            "address": addr,
            "adptDt": regdt,
            "warnings": ws,
        })
        if ws:
            signals.append({"workplace": nm, "warnings": ws})

    overall_severity = "high" if "high" in severities else ("medium" if "medium" in severities else None)
    return {
        "available": True,
        "matched": len(items),
        "summary": summary,
        "signals": signals,
        "overall_severity": overall_severity,
    }


class PensionIn(BaseModel):
    query: str
    add_to_cluster: bool = True


def _local_search(query: str) -> list[dict]:
    """SQLite에 적재된 NPS CSV 데이터에서 검색."""
    is_bno = query.isdigit() and len(query) == 10
    if is_bno:
        sql = "SELECT * FROM nps_workplaces WHERE bzowr_rgst_no = ? LIMIT 30"
        args: tuple = (query,)
    else:
        import re as _re
        norm = _re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", query).lower()
        if not norm:
            return []
        sql = "SELECT * FROM nps_workplaces WHERE wkpl_nm_norm LIKE ? LIMIT 30"
        args = (f"%{norm}%",)
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def _items_from_local(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "wkplNm": r["wkpl_nm"],
            "bzowrRgstNo": r["bzowr_rgst_no"],
            "jnngpCnt": r["subscriber_cnt"],
            "newSubscbrCnt": r["new_cnt"],
            "lossSubscbrCnt": r["lost_cnt"],
            "avrgPay": r["avg_pay"],
            "ldongAddrMgplDgCdNm": r["addr"] or "",
            "adptDt": r["adpt_dt"],
            "_industry": r["industry"],
        })
    return out


@router.post("/scan")
def scan(inp: PensionIn) -> dict:
    if not os.environ.get("DATA_GO_KR_KEY"):
        return {"available": False, "reason": "DATA_GO_KR_KEY 미설정"}
    if len(inp.query.strip()) < 2:
        return {"available": True, "reason": "검색어 2자 이상"}

    items, status, dt = search_enrolled(inp.query.strip())
    used_source = "api"

    # API 실패 시 로컬 CSV 폴백
    if status != 200 or not items:
        local_rows = _local_search(inp.query.strip())
        if local_rows:
            items = _items_from_local(local_rows)
            used_source = "local_csv"

    if used_source == "api" and status != 200 and not items:
        return {
            "available": False,
            "reason": f"NPS API 응답 실패({status}) + 로컬 CSV 미적재. 두 경로 다 실패.",
            "fix_api": "https://www.data.go.kr/data/3046071/openapi.do 활용신청 (자동승인 + 최대 1시간 propagation)",
            "fix_csv": "https://www.data.go.kr/data/15083277/fileData.do 다운로드 → samples/ 에 두고  python -m scripts.ingest_nps  실행",
            "fetched_in_ms": dt,
            "status": status,
        }

    if not items:
        return {
            "available": True,
            "matched": 0,
            "source": used_source,
            "fetched_in_ms": dt,
            "note": "사업장명 매칭 결과 없음. 정확 명칭 필요 — '주식회사' 같은 접두 제거 후 재검색.",
        }

    out = signals_from_enrollment(items)
    out["fetched_in_ms"] = dt
    out["status"] = status
    out["source"] = used_source

    sev = out.get("overall_severity")
    if inp.add_to_cluster and sev:
        # 신호 종류별로 도메인 분리
        signals = out.get("signals", [])
        domains_seen: set[str] = set()
        for sg in signals:
            for w in sg.get("warnings", []):
                txt = w.get("sig", "")
                if "회전율" in txt:
                    domains_seen.add("hiring")
                elif "이탈" in txt or "상실" in txt:
                    domains_seen.add("closure")
                elif "저임금" in txt:
                    domains_seen.add("finance")
        if not domains_seen:
            domains_seen.add("hiring")  # 기본 — 가입자 흐름이 있는 한 hiring 도메인
        for d in domains_seen:
            add_signal(
                company_raw=inp.query.strip(),
                channel="pension", domain=d,
                severity=sev,
                source_ref=f"NPS:매칭{out['matched']}/신호{len(signals)}",
            )
    return out


@router.get("/scan")
def scan_get(q: str) -> dict:
    return scan(PensionIn(query=q, add_to_cluster=False))


# ──────────────────────────────────────────────
# 탈퇴사업장 (data.go.kr 15020284) — 폐업·해산 추적
# ──────────────────────────────────────────────

def search_withdrawn(name_or_bno: str) -> tuple[list[dict], int, int]:
    is_bno = name_or_bno.isdigit() and len(name_or_bno) == 10
    params = {"bzowrRgstNo": name_or_bno} if is_bno else {"wkpl_nm": name_or_bno}
    data, status, dt = _call_nps(WITHDRAWN, params)
    return _items_from(data or {}), status, dt


@router.get("/withdrawn")
def withdrawn(q: str) -> dict:
    """탈퇴(폐업)된 사업장 조회 — closure 도메인 강신호."""
    if not os.environ.get("DATA_GO_KR_KEY"):
        return {"available": False, "reason": "DATA_GO_KR_KEY 미설정"}
    items, status, dt = search_withdrawn(q.strip())

    if status != 200:
        return {
            "available": False,
            "reason": f"data.go.kr NPS 탈퇴사업장 API 응답 실패({status})",
            "fix": "https://www.data.go.kr/data/15020284/openapi.do 에서 활용신청 → 자동승인",
            "fetched_in_ms": dt,
        }

    if not items:
        return {"available": True, "matched": 0, "fetched_in_ms": dt}

    out = []
    for it in items[:20]:
        out.append({
            "wkplNm": it.get("wkplNm", ""),
            "bzowrRgstNo": it.get("bzowrRgstNo", ""),
            "addr": it.get("ldongAddrMgplDgCdNm", "") or it.get("rdnmAddr", ""),
            "withdraw_date": it.get("scdrDt", "") or it.get("erpDt", ""),
            "industry": it.get("indutyNmCd", "") or it.get("indutyNm", ""),
        })

    # closure 도메인 신호
    if items:
        add_signal(
            company_raw=q.strip(),
            channel="pension", domain="closure",
            severity="high",
            source_ref=f"NPS탈퇴:{len(items)}건",
        )

    return {
        "available": True,
        "matched": len(items),
        "fetched_in_ms": dt,
        "withdrawn": out,
    }


# ──────────────────────────────────────────────────────────────────
# 시계열 Z-score 경보 — 월별 가입자 급감 탐지
# ──────────────────────────────────────────────────────────────────

def _zscore(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    if std == 0:
        return [0.0] * len(values)
    return [round((v - mean) / std, 2) for v in values]


@router.get("/timeseries")
def timeseries(q: str = "하늘") -> dict:
    """사업장명 → NPS 월별 가입자 수 시계열 + Z-score 급감 경보.

    nps_workplaces 테이블의 snapshot_ym 컬럼으로 월별 추이 구성.
    Z-score < -2 인 월 = 가입자 급감 이상 신호.
    """
    import re as _re
    norm = _re.sub(r"[\s\(\)（）\[\]【】·,\.\-_/]", "", q).lower()
    if not norm:
        return {"available": False, "reason": "검색어 없음"}

    with conn() as c:
        rows = c.execute(
            "SELECT snapshot_ym, subscriber_cnt, new_cnt, lost_cnt, avg_pay "
            "FROM nps_workplaces WHERE wkpl_nm_norm LIKE ? "
            "ORDER BY snapshot_ym ASC LIMIT 36",
            (f"%{norm}%",),
        ).fetchall()

    if not rows:
        return {
            "available": False,
            "reason": "NPS 시계열 데이터 없음 (CSV 미적재 또는 사업장명 불일치)",
            "fix": "data.go.kr 국민연금 사업장 CSV를 samples/에 넣고 python -m scripts.ingest_nps 실행",
            "note": "가짜 시계열은 생성하지 않습니다. 실제 적재 데이터만 표시합니다.",
        }

    subs = [int(r["subscriber_cnt"] or 0) for r in rows]
    zscores = _zscore([float(s) for s in subs])
    alerts: list[dict] = []

    series = []
    for i, r in enumerate(rows):
        z = zscores[i]
        entry = {
            "ym": r["snapshot_ym"],
            "subscribers": r["subscriber_cnt"],
            "new": r["new_cnt"],
            "lost": r["lost_cnt"],
            "avg_pay": r["avg_pay"],
            "zscore": z,
        }
        if z <= -2.0:
            entry["alert"] = "급감"
            alerts.append({
                "ym": r["snapshot_ym"],
                "zscore": z,
                "subscribers": r["subscriber_cnt"],
                "label": f"{r['snapshot_ym']} 가입자 {r['subscriber_cnt']}명 — Z={z:.2f} (급감 경보)",
            })
        elif z <= -1.5:
            entry["alert"] = "주의"
        series.append(entry)

    # 이탈률 추세 (최근 3개월 평균 vs 전기 평균)
    trend_signal = None
    if len(subs) >= 6:
        recent = sum(subs[-3:]) / 3
        prior  = sum(subs[-6:-3]) / 3
        if prior > 0:
            chg = round((recent - prior) / prior * 100, 1)
            if chg <= -20:
                trend_signal = {"label": f"최근 3개월 평균 가입자 전기 대비 {chg}% 감소", "severity": "high"}
            elif chg <= -10:
                trend_signal = {"label": f"최근 3개월 평균 가입자 전기 대비 {chg}% 감소", "severity": "medium"}

    return {
        "available": True,
        "query": q,
        "periods": len(series),
        "series": series,
        "alerts": alerts,
        "trend": trend_signal,
        "method": "Z-score < -2.0 = 가입자 급감 경보 (정규분포 기준 상위 2.3%)",
    }


@router.get("/summary")
def summary() -> dict:
    """홈 대시보드용 NPS 집계 요약."""
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM nps_workplaces").fetchone()[0]
        alert_cnt = c.execute(
            """SELECT COUNT(*) FROM nps_workplaces
               WHERE subscriber_cnt > 0
                 AND (CAST(lost_cnt AS REAL)/subscriber_cnt) >= 0.20
                 AND avg_pay < 1800000"""
        ).fetchone()[0]
    return {
        "total_workplaces": total,
        "zscore_alert_count": alert_cnt,
    }


@router.get("/top-risk")
def top_risk(limit: int = 20) -> dict:
    """고위험 사업장 TOP N — 저임금 + 고이탈률 복합 위험 점수 순위.

    위험점수 = 이탈률(%) × 0.6 + 저임금지수(1-avg_pay/2,060,000) × 40
    최저임금 2024: 월 2,060,240원 기준.
    """
    with conn() as c:
        rows = c.execute(
            """SELECT wkpl_nm, region_dg, industry,
                      subscriber_cnt, lost_cnt, new_cnt, avg_pay
               FROM nps_workplaces
               WHERE subscriber_cnt > 0
                 AND avg_pay > 0 AND avg_pay < 1900000
                 AND lost_cnt > 0
               ORDER BY (CAST(lost_cnt AS REAL) / subscriber_cnt) DESC, avg_pay ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    items = []
    min_wage_monthly = 2_060_240
    for r in rows:
        churn = r["lost_cnt"] / max(r["subscriber_cnt"], 1)
        wage_ratio = max(0.0, 1.0 - r["avg_pay"] / min_wage_monthly)
        risk_score = int(min(100, round(churn * 60 + wage_ratio * 40)))
        items.append({
            "name": r["wkpl_nm"],
            "region": r["region_dg"] or "",
            "industry": r["industry"] or "",
            "subscribers": r["subscriber_cnt"],
            "lost": r["lost_cnt"],
            "churn_pct": round(churn * 100, 1),
            "avg_pay": r["avg_pay"],
            "risk_score": risk_score,
        })

    return {
        "available": True,
        "count": len(items),
        "items": items,
        "snapshot_ym": "202504",
        "method": "이탈률×0.6 + 저임금지수×0.4 → 위험점수 (최저임금 2,060,240원 기준)",
    }


@router.get("/demo-timeseries")
def demo_timeseries(company: str = "") -> dict:
    """실제 체불사업주 기반 NPS 선행지표 시뮬레이션.

    실제 공개 체불명단에서 회사를 조회하고, 체불 발생 3~6개월 전부터 NPS 가입자 수가
    감소하는 패턴을 Beta/정규분포로 시뮬레이션합니다.
    (실 NPS CSV 미적재 시 연구·시연 목적 시뮬레이션임을 명시)
    """
    import math, random
    from datetime import datetime

    # 체불사업주 DB에서 조회
    with conn() as c:
        if company:
            import re as _re
            norm = _re.sub(r"[\s\(\)（）\[\]【】·,\.\-_/]", "", company).lower()
            rows = c.execute(
                "SELECT company, amount, year FROM defaulters WHERE LOWER(REPLACE(REPLACE(company,'(',''),')','' )) LIKE ? LIMIT 1",
                (f"%{norm}%",),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT company, amount, year FROM defaulters ORDER BY amount DESC LIMIT 1"
            ).fetchall()

    if not rows:
        # 검색어와 무관하게 최고 체불액 1건으로 시연
        with conn() as c:
            rows = c.execute("SELECT company, amount, year FROM defaulters ORDER BY amount DESC LIMIT 1").fetchall()
    if not rows:
        return {"available": False, "reason": "체불사업주 DB에서 해당 회사를 찾을 수 없습니다."}

    row = rows[0]
    biz = row["company"]
    amount = row["amount"] or 0
    det_year = int(row["year"]) if row["year"] else 2026
    det_month = 3  # 체불 적발 월 (가정)

    # 12개월 시뮬 — 적발 6개월 전부터 가입자 감소 시작
    random.seed(hash(biz) & 0x7FFFFFFF)
    base = random.randint(80, 300)  # 기저 가입자 수
    series = []
    months = []
    for i in range(12):
        month_offset = i - 11  # -11 ~ 0 (11개월 전부터 당월)
        abs_month = det_month + month_offset
        abs_year = det_year
        if abs_month <= 0:
            abs_month += 12
            abs_year -= 1
        elif abs_month > 12:
            abs_month -= 12
            abs_year += 1
        ym = f"{abs_year}{abs_month:02d}"
        months.append(ym)

        # 가입자 수: 6개월 전부터 선형 감소
        if month_offset >= -5:
            decay = 1.0 + (month_offset + 5) * (-0.08 + random.gauss(0, 0.02))
            subs = max(5, int(base * max(0.2, decay) + random.gauss(0, 3)))
        else:
            subs = max(10, base + int(random.gauss(0, 5)))

        lost = max(0, int(subs * random.uniform(0.02, 0.05)) if month_offset < -5 else int(subs * random.uniform(0.08, 0.25)))
        new_in = max(0, int(lost * random.uniform(0.3, 0.9)) if month_offset >= -5 else int(lost * random.uniform(0.8, 1.2)))
        avg_pay = max(800000, int(1_200_000 + random.gauss(0, 100_000) - (max(0, month_offset + 5) * 30_000)))
        series.append({"ym": ym, "subscribers": subs, "new": new_in, "lost": lost, "avg_pay": avg_pay})

    # Z-score
    subs_vals = [s["subscribers"] for s in series]
    mean_s = sum(subs_vals) / len(subs_vals)
    std_s = math.sqrt(sum((v - mean_s) ** 2 for v in subs_vals) / len(subs_vals)) or 1.0
    alerts = []
    for i, s in enumerate(series):
        z = round((s["subscribers"] - mean_s) / std_s, 2)
        s["zscore"] = z
        if z <= -2.0:
            s["alert"] = "급감 경보"
            alerts.append({"ym": s["ym"], "zscore": z, "label": f"{s['ym']} 가입자 {s['subscribers']}명 Z={z} (급감 경보)"})
        elif z <= -1.5:
            s["alert"] = "주의"

    det_ym = f"{det_year}{det_month:02d}"
    return {
        "available": True,
        "is_simulation": True,
        "sim_note": "실 NPS CSV 미적재 — 체불 등재 사업장 기반 선행지표 시뮬레이션 (연구·시연 목적)",
        "company": biz,
        "amount_wan": amount // 10_000,
        "detected_ym": det_ym,
        "series": series,
        "alerts": alerts,
        "method": "체불 등재 3~6개월 전 NPS 가입자 감소 패턴 (Beta 분포 시뮬). 실 CSV 적재 시 실 데이터로 대체됩니다.",
    }

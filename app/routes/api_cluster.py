"""A축 사업장 신호 집계 — 신고·워치·셀프체크·진단 누적 → 클러스터 결성"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException

from ..db import conn
from .api_notify import push_notification

router = APIRouter(prefix="/api/cluster")

CHANNEL_WEIGHT = {
    "case":      30,
    "pension":   28,
    "watch":     20,
    "external":  18,
    "diagnosis": 15,
    "selfcheck": 10,
}

SEVERITY_MULT = {"low": 0.5, "medium": 1.0, "high": 1.5}


def time_weight(created_at: str | None) -> float:
    """TRIZ #15 동적특성 — 최근 신호 가중. 30일 이내 1.5x / 30~90일 1.0x / 90일+ 0.5x."""
    if not created_at:
        return 1.0
    try:
        from datetime import datetime
        t = datetime.fromisoformat(created_at)
        age = (datetime.now() - t).days
    except Exception:
        return 1.0
    if age <= 30:
        return 1.5
    if age <= 90:
        return 1.0
    if age <= 180:
        return 0.7
    return 0.5

DOMAINS = {
    "pay_default": "체불 의심",
    "hiring":      "이직 많음",
    "closure":     "폐업 위험",
    "finance":     "자금 위기",
    "reputation":  "평판 부정",
    "meta":        "관심 등록",
}

ALERT_N = 3
ALERT_MIN_DOMAINS = 2   # 진짜 체불 의심 alert는 도메인 ≥2개 필요 (단일 도메인은 약함)


def normalize(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"[\s\(\)（）\[\]【】《》・·,\.\-_/]", "", name)
    s = re.sub(r"^(주식회사|㈜|유한회사|합자회사|법인|\(주\))", "", s)
    s = s.replace("주식회사", "")
    return s.lower()


def add_signal(
    company_raw: str,
    channel: str,
    domain: str,
    severity: str = "medium",
    source_ref: str | None = None,
    region: str | None = None,
    industry: str | None = None,
    event_at: str | None = None,
) -> dict:
    if domain not in DOMAINS:
        domain = "meta"
    norm = normalize(company_raw)
    if not norm:
        return {"skipped": "empty company name"}
    now = datetime.now().isoformat(timespec="seconds")

    with conn() as c:
        c.execute(
            """INSERT INTO company_signals
               (company_norm, company_raw, channel, domain, severity, source_ref,
                region, industry, event_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (norm, company_raw, channel, domain, severity, source_ref,
             region, industry, event_at, now),
        )
        rows = c.execute(
            "SELECT channel, domain, severity, created_at FROM company_signals WHERE company_norm=?",
            (norm,),
        ).fetchall()

    score = sum(
        CHANNEL_WEIGHT.get(r["channel"], 0)
        * SEVERITY_MULT.get(r["severity"], 1.0)
        * time_weight(r["created_at"])
        for r in rows
    )
    n = len(rows)
    distinct_channels = len({r["channel"] for r in rows})
    # 도메인은 의미적 분류. meta(사용자 관심) 제외하고 카운트.
    real_domains = {r["domain"] for r in rows if r["domain"] and r["domain"] != "meta"}
    distinct_domains = len(real_domains)

    # 자동 알림 — 단일 도메인은 약함. 도메인 ≥2 필수 (체불 의심으로 격상 시점)
    if n >= ALERT_N and distinct_channels >= 2 and distinct_domains >= ALERT_MIN_DOMAINS:
        with conn() as c:
            prev = c.execute(
                "SELECT last_alert_n FROM clusters_alerted WHERE company_norm=?", (norm,)
            ).fetchone()
            should_alert = (prev is None) or (n - (prev["last_alert_n"] or 0) >= 2)
            if should_alert:
                c.execute(
                    """INSERT OR REPLACE INTO clusters_alerted
                       (company_norm, last_alert_n, last_alert_at) VALUES (?,?,?)""",
                    (norm, n, now),
                )
        if should_alert:
            domain_labels = ", ".join(DOMAINS.get(d, d) for d in sorted(real_domains))
            push_notification(
                audience="supervisor",
                severity="critical" if score >= 90 else "warning",
                title=f"🚨 다중 도메인 신호 결합: {company_raw} (N={n}, 도메인 {distinct_domains})",
                body=f"도메인: {domain_labels} · 독립 채널 {distinct_channels}개 · 누적 점수 {int(score)}",
                link=f"/cluster/{normalize(company_raw)}",
            )

    # TRIZ #20 — 같은 사업장 워치 등록자에 자동 broadcast (severity high인 신규 신호만)
    if severity == "high" and channel != "watch":
        try:
            with conn() as c:
                watchers = c.execute(
                    """SELECT DISTINCT label FROM watchlist
                       WHERE company_query LIKE ? OR company_query LIKE ?""",
                    (company_raw, f"%{company_raw}%"),
                ).fetchall()
            if watchers:
                push_notification(
                    audience="worker",
                    severity="warning",
                    title=f"📡 워치 사업장 신규 위험 신호 — {company_raw}",
                    body=f"채널 {channel}/도메인 {domain} 신호 high. 워치 등록자 {len(watchers)}명에 통지.",
                    link=f"/company/{company_raw}",
                )
        except Exception:
            pass

        # 사업주 구독자에도 알림
        try:
            with conn() as c:
                subs = c.execute(
                    "SELECT id, owner_name FROM owner_subscriptions WHERE company_norm = ?",
                    (norm,),
                ).fetchall()
            for s in subs:
                push_notification(
                    audience="owner",
                    severity="warning",
                    title=f"⚠ 자가구독 사업장 위험 신호 — {company_raw}",
                    body=f"channel={channel} domain={domain}. 소명 가능합니다.",
                    link=f"/owner-notice/{company_raw}",
                )
        except Exception:
            pass

    return {
        "company_norm": norm,
        "n_signals": n,
        "distinct_channels": distinct_channels,
        "distinct_domains": distinct_domains,
        "domains": sorted(real_domains),
        "score": int(score),
        "alert_threshold_n": ALERT_N,
        "alert_min_domains": ALERT_MIN_DOMAINS,
    }


@router.get("/_meta/domains")
def meta_domains() -> dict:
    return {"domains": DOMAINS, "alert_n": ALERT_N, "alert_min_domains": ALERT_MIN_DOMAINS}


@router.get("")
def list_clusters(min_n: int = 1) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT company_norm,
                      MAX(company_raw) AS company,
                      COUNT(*) AS n,
                      COUNT(DISTINCT channel) AS distinct_channels,
                      COUNT(DISTINCT CASE WHEN domain IS NOT NULL AND domain != 'meta' THEN domain END) AS distinct_domains,
                      MAX(created_at) AS last_seen,
                      GROUP_CONCAT(DISTINCT channel) AS channels,
                      GROUP_CONCAT(DISTINCT domain) AS domain_list,
                      MAX(region) AS region, MAX(industry) AS industry
               FROM company_signals
               GROUP BY company_norm
               HAVING n >= ?
               ORDER BY distinct_domains DESC, n DESC, last_seen DESC""",
            (min_n,),
        ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        with conn() as c:
            details = c.execute(
                "SELECT channel, severity, domain, created_at FROM company_signals WHERE company_norm=?",
                (r["company_norm"],),
            ).fetchall()
        r["score"] = int(sum(
            CHANNEL_WEIGHT.get(d["channel"], 0)
            * SEVERITY_MULT.get(d["severity"], 1.0)
            * time_weight(d["created_at"])
            for d in details
        ))
        # 도메인별 카운트
        dom_count: dict[str, int] = {}
        for d in details:
            dom_count[d["domain"] or "meta"] = dom_count.get(d["domain"] or "meta", 0) + 1
        r["domain_counts"] = dom_count
        out.append(r)
    return out


@router.get("/{norm}")
def cluster_detail(norm: str) -> dict:
    with conn() as c:
        sigs = c.execute(
            """SELECT id, company_raw, channel, domain, severity, source_ref,
                      region, industry, event_at, created_at
               FROM company_signals WHERE company_norm=? ORDER BY id DESC""",
            (norm,),
        ).fetchall()
    if not sigs:
        raise HTTPException(404, "cluster not found")
    sigs = [dict(s) for s in sigs]
    score = int(sum(
        CHANNEL_WEIGHT.get(s["channel"], 0)
        * SEVERITY_MULT.get(s["severity"], 1.0)
        * time_weight(s.get("created_at"))
        for s in sigs
    ))

    # 도메인별 분해
    by_domain: dict[str, list[dict]] = {}
    for s in sigs:
        d = s["domain"] or "meta"
        by_domain.setdefault(d, []).append(s)

    real_domains = [d for d in by_domain.keys() if d != "meta"]

    return {
        "company_norm": norm,
        "company": sigs[0]["company_raw"],
        "signals": sigs,
        "n_signals": len(sigs),
        "distinct_channels": len({s["channel"] for s in sigs}),
        "distinct_domains": len(real_domains),
        "by_domain": {d: {"count": len(items), "label": DOMAINS.get(d, d), "items": items} for d, items in by_domain.items()},
        "score": score,
        "alert_eligible": (
            len(sigs) >= ALERT_N
            and len({s["channel"] for s in sigs}) >= 2
            and len(real_domains) >= ALERT_MIN_DOMAINS
        ),
    }

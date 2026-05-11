"""외부 신호 — 네이버 검색 API로 사업장 관련 후기·뉴스·블로그 본문에서 위험 키워드 추출"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from .api_business import log_call
from .api_cluster import add_signal

router = APIRouter(prefix="/api/external")

NAVER_BASE = "https://openapi.naver.com/v1/search"
SOURCES = ["news", "blog", "cafearticle", "kin"]
SOURCE_LABEL = {"news": "뉴스", "blog": "블로그", "cafearticle": "카페", "kin": "지식인"}

STRONG_KEYWORDS = [
    # 직접 체불 표현
    "임금체불", "월급체불", "월급체납", "체불임금", "체임", "체불 임금",
    "월급 안 줘", "월급 안 주", "월급 못 받", "월급 미지급", "월급 안나",
    "임금 안 줘", "임금 안 주", "임금 못 받", "임금 미지급",
    "퇴직금 미지급", "퇴직금 안 줘", "퇴직금 안 주", "퇴직금 못 받",
    "급여 미지급", "급여 안 줘",
    # 노동부·신고
    "노동부", "고용노동부", "노동청", "근로감독", "근로기준법 위반",
    "임금체불 신고", "체불 신고", "노동부 신고", "고용노동부 신고", "노동청 신고",
    "체불 진정", "임금체불 진정", "근로감독관",
    # 사업주 도주·폐업
    "사장 잠수", "사장이 잠수", "야반도주", "갑자기 폐업", "사업장 폐업",
    "회사 도망", "법정관리", "회생절차", "기업회생",
    # 지급 지연
    "급여 지연", "월급 지연", "임금 지연", "월급 늦", "월급이 늦", "급여가 늦",
    # 수당
    "주휴수당 안", "연차수당 안", "야근수당 안", "수당 미지급", "수당 안 줘",
    # 다수 신호
    "직원 다 그만", "대량 퇴사", "줄퇴사", "회사 망",
]

# 약 키워드 — 회사명과 동시에 가까이 등장할 때만 가중
WEAK_KEYWORDS = [
    "퇴사", "그만뒀", "그만뒀어요", "안 줘", "못 받", "도망", "잠수",
    "갑질", "꼰대", "야근 강요",
]

JOB_POSTING_HINTS = [
    "채용", "모집", "구인", "사원모집", "직원 모집", "공고",
    "신입", "경력", "인턴", "수습", "정규직", "계약직",
    "이력서", "자기소개서", "면접",
]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _has_risk(text: str, company: str | None = None) -> tuple[int, list[str]]:
    """강 키워드는 무조건, 약 키워드는 회사명이 같이 등장할 때만."""
    matched: list[str] = []
    for kw in STRONG_KEYWORDS:
        if kw in text and kw not in matched:
            matched.append(kw)
    if company and len(company) >= 2:
        in_company = company in text
        if in_company:
            for kw in WEAK_KEYWORDS:
                if kw in text and kw not in matched:
                    matched.append(kw)
    return len(matched), matched


# 하위 호환
RISK_KEYWORDS = STRONG_KEYWORDS


def search_naver(query: str, source: str, display: int = 10) -> tuple[list[dict], int, int]:
    """네이버 검색 API 단일 소스 호출. (items, status, ms)"""
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        return [], 0, 0

    t0 = time.time()
    try:
        r = requests.get(
            f"{NAVER_BASE}/{source}.json",
            params={"query": query, "display": display, "sort": "date" if source == "news" else "sim"},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
            timeout=10,
        )
        dt = int((time.time() - t0) * 1000)
        log_call(f"NAVER-{source}", f"{NAVER_BASE}/{source}.json", r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return [], r.status_code, dt
        return r.json().get("items", []) or [], 200, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call(f"NAVER-{source}", f"{NAVER_BASE}/{source}.json", 0, dt, False)
        return [], 0, dt


def _scan_items(items: list[dict], source: str, company: str | None, kind: str) -> list[dict]:
    out: list[dict] = []
    for it in items:
        title = _strip_html(it.get("title", ""))
        desc = _strip_html(it.get("description", ""))
        body = title + " " + desc
        n, kws = _has_risk(body, company)
        if n == 0:
            continue
        out.append({
            "source": source,
            "kind": kind,
            "title": title,
            "description": desc[:200],
            "link": it.get("link") or it.get("originallink"),
            "pub": it.get("pubDate") or it.get("postdate"),
            "matched_keywords": kws,
            "match_count": n,
            "company_in_body": (company or "") in body,
        })
    return out


_GENERIC_JOB_WORDS = [
    "채용", "모집", "공고", "구인", "사원", "정규직", "계약직", "인턴", "신입", "경력",
    "안내", "공지", "지원", "마감", "임박", "중", "소개", "이력서", "자기소개서",
    "수시", "상시", "공채", "직원", "급구", "추가", "재공고", "필수", "우대", "환영",
]


def _normalize_job_title(title: str, company: str) -> str:
    """공고 제목 정규화 — 회사명/날짜/일반 직무용어 제거 후 핵심 직군만 남김."""
    if not title:
        return ""
    s = title
    s = re.sub(r"\d+", "", s)
    s = re.sub(r"[(){}\[\]<>《》【】※·,\.\-_/~|·\!\?]", " ", s)
    s = s.replace(company, "")
    for w in _GENERIC_JOB_WORDS:
        s = s.replace(w, " ")
    return " ".join(s.split()).strip().lower()


def _check_company_presence(company: str) -> dict:
    """채용 외 흔적이 있는지 확인 — 실체 검증.

    원칙: '공고 시점 + 그 이후에 비채용 정보가 있어야 실체 있음'.
    채용만 있고 비채용 흔적이 거의 없으면 위장채용·페이퍼컴퍼니 패턴.
    """
    if not (os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")):
        return {"available": False}

    fetched_ms = 0
    seen_links: set[str] = set()
    hiring_posts: list[dict] = []
    non_hiring_posts: list[dict] = []

    for src in ("blog", "cafearticle", "news", "kin"):
        items, _, dt = search_naver(company, src, display=20)
        fetched_ms += dt
        for it in items:
            title = _strip_html(it.get("title", ""))
            desc = _strip_html(it.get("description", ""))
            body = title + " " + desc
            if company not in body:
                continue
            link = it.get("link") or it.get("originallink") or ""
            if link in seen_links:
                continue
            seen_links.add(link)
            pub = it.get("pubDate") or it.get("postdate") or ""
            entry = {
                "source": src,
                "title": title,
                "description": desc[:160],
                "link": link,
                "pub": pub,
            }
            is_hiring = any(h in body for h in JOB_POSTING_HINTS)
            if is_hiring:
                hiring_posts.append(entry)
            else:
                non_hiring_posts.append(entry)

    total = len(hiring_posts) + len(non_hiring_posts)
    nh = len(non_hiring_posts)
    h = len(hiring_posts)
    nh_ratio = (nh / total) if total else None

    severity: str | None = None
    reason = None
    is_suspicious = False
    if h >= 5 and nh <= 1:
        severity = "high"
        is_suspicious = True
        reason = f"일반 검색 {total}건 중 채용공고 {h}건, 비채용 정보 {nh}건 — 실체 흔적 거의 없음"
    elif h >= 3 and nh == 0:
        severity = "medium"
        is_suspicious = True
        reason = f"검색 결과 {total}건 모두 채용 관련 — 비채용 흔적 0건"
    elif h >= 2 and nh_ratio is not None and nh_ratio < 0.2 and total >= 5:
        severity = "low"
        is_suspicious = True
        reason = f"비채용 정보 비율 {round(nh_ratio*100)}% — 실체 약함"

    # 시점 보정 — 채용 게시 후 비채용 콘텐츠가 있는가?
    timeline_ok = False
    latest_hiring_pub = None
    later_non_hiring_count = 0
    if hiring_posts and non_hiring_posts:
        try:
            hiring_dates = [p["pub"] for p in hiring_posts if p.get("pub")]
            if hiring_dates:
                latest_hiring_pub = max(hiring_dates)
                later_non_hiring_count = sum(
                    1 for p in non_hiring_posts
                    if p.get("pub") and p["pub"] > latest_hiring_pub
                )
                timeline_ok = later_non_hiring_count >= 1
        except Exception:
            timeline_ok = False

    return {
        "available": True,
        "total": total,
        "hiring": h,
        "non_hiring": nh,
        "non_hiring_ratio": round(nh_ratio, 3) if nh_ratio is not None else None,
        "suspicious": is_suspicious,
        "severity": severity,
        "reason": reason,
        "timeline": {
            "latest_hiring_pub": latest_hiring_pub,
            "non_hiring_after_latest_hiring": later_non_hiring_count,
            "real_existence_after_hiring": timeline_ok,
        },
        "samples_non_hiring": non_hiring_posts[:5],
        "fetched_in_ms": fetched_ms,
    }


def _detect_repeat_postings(company: str) -> dict:
    """회사명 + 채용 검색 → 동일 자리 반복 모집 패턴 탐지.

    경험적 신호: '같은 직군을 반복적으로/짧은 주기로 채용' = 회전율 매우 높음 = 체불·갑질 위험.
    """
    if not (os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")):
        return {"available": False}

    queries = [f"{company} 채용", f"{company} 모집", f"{company} 공고"]
    all_posts: list[dict] = []
    fetched_ms = 0

    seen_links: set[str] = set()
    for q in queries:
        for src in ("blog", "cafearticle"):
            items, _, dt = search_naver(q, src, display=20)
            fetched_ms += dt
            for it in items:
                title = _strip_html(it.get("title", ""))
                desc = _strip_html(it.get("description", ""))
                body = title + " " + desc
                if company not in body:
                    continue
                if not any(h in body for h in JOB_POSTING_HINTS):
                    continue
                link = it.get("link") or it.get("originallink") or ""
                if link in seen_links:
                    continue
                seen_links.add(link)
                norm = _normalize_job_title(title, company)
                if len(norm) < 2:
                    continue
                all_posts.append({
                    "source": src,
                    "title": title,
                    "norm": norm,
                    "link": link,
                    "pub": it.get("pubDate") or it.get("postdate"),
                    "description": desc[:160],
                })

    # 동일 직군 그룹화
    groups: dict[str, list[dict]] = {}
    for p in all_posts:
        groups.setdefault(p["norm"], []).append(p)

    # 반복 그룹 (같은 norm 2건 이상)
    repeats: list[dict] = []
    for norm, posts in groups.items():
        if len(posts) < 2:
            continue
        posts_sorted = sorted(posts, key=lambda x: x.get("pub") or "", reverse=True)
        repeats.append({
            "role_keyword": norm[:80],
            "count": len(posts),
            "first_pub": (posts_sorted[-1].get("pub") or ""),
            "last_pub": (posts_sorted[0].get("pub") or ""),
            "posts": posts_sorted,
        })
    repeats.sort(key=lambda g: -g["count"])

    total_hits = len(all_posts)
    repeat_count = sum(g["count"] for g in repeats)
    max_group = max((g["count"] for g in repeats), default=0)
    distinct_groups = len(groups)

    severity: str | None = None
    if max_group >= 5 or repeat_count >= 8:
        severity = "high"   # 한 자리 5번 이상 다시 올림 = 매우 위험
    elif max_group >= 3 or repeat_count >= 4 or total_hits >= 8:
        severity = "medium"
    elif total_hits >= 3:
        severity = "low"

    return {
        "available": True,
        "company": company,
        "total_posts": total_hits,
        "distinct_groups": distinct_groups,
        "repeat_count": repeat_count,
        "max_repeat": max_group,
        "repeats": repeats[:6],
        "samples": all_posts[:5],
        "severity": severity,
        "fetched_in_ms": fetched_ms,
    }


def _targeted_search(company: str) -> tuple[list[dict], int]:
    """'회사명 + 위험 키워드' 조합 검색 — 더 정확한 매칭."""
    targeted_keywords = ["체불", "미지급", "노동부", "신고", "월급", "퇴사"]
    findings: list[dict] = []
    fetched_ms = 0
    for kw in targeted_keywords:
        for src in ("blog", "cafearticle"):
            items, _, dt = search_naver(f"{company} {kw}", src, display=10)
            fetched_ms += dt
            for it in items:
                title = _strip_html(it.get("title", ""))
                desc = _strip_html(it.get("description", ""))
                body = title + " " + desc
                if company not in body:
                    continue
                n, kws = _has_risk(body, company)
                if n == 0:
                    continue
                findings.append({
                    "source": src,
                    "kind": "targeted",
                    "title": title,
                    "description": desc[:200],
                    "link": it.get("link") or it.get("originallink"),
                    "pub": it.get("pubDate") or it.get("postdate"),
                    "matched_keywords": kws,
                    "match_count": n,
                    "company_in_body": True,
                    "targeted_with": kw,
                })
    # 중복 제거 (link 기준)
    seen = set()
    deduped = []
    for f in sorted(findings, key=lambda x: -x["match_count"]):
        if f["link"] in seen:
            continue
        seen.add(f["link"])
        deduped.append(f)
    return deduped, fetched_ms


def _aggregate(query: str) -> dict:
    if not (os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")):
        return {
            "available": False,
            "reason": ".env에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요",
        }

    all_findings: list[dict] = []
    counts: dict[str, dict] = {}
    total_ms = 0
    # 1. 일반 검색 (회사명만)
    for src in SOURCES:
        items, status, dt = search_naver(query, src)
        total_ms += dt
        scanned = _scan_items(items, src, query, kind="generic")
        all_findings.extend(scanned)
        counts[src] = {
            "fetched": len(items),
            "matched": len(scanned),
            "status": status,
        }
    # 2. 타겟 검색 (회사명 + 위험키워드)
    targeted, t_ms = _targeted_search(query)
    total_ms += t_ms
    all_findings.extend(targeted)
    # 3. 채용공고 반복 패턴
    job = _detect_repeat_postings(query)
    if job.get("available"):
        total_ms += job.get("fetched_in_ms", 0)

    # 4. 실체 검증 — 채용 외 흔적이 있는가
    presence = _check_company_presence(query)
    if presence.get("available"):
        total_ms += presence.get("fetched_in_ms", 0)

    # 중복 제거
    seen_links = set()
    deduped: list[dict] = []
    for f in sorted(all_findings, key=lambda x: (-x["match_count"], -int(x.get("company_in_body", False)))):
        if f.get("link") in seen_links:
            continue
        seen_links.add(f.get("link"))
        deduped.append(f)

    n_match = len(deduped)
    n_company_match = sum(1 for f in deduped if f.get("company_in_body"))
    sum_kw = sum(f["match_count"] for f in deduped)
    n_targeted = sum(1 for f in deduped if f.get("kind") == "targeted")

    # 채용 반복 점수 — 같은 자리 N번 = N^1.4 가중
    job_score = 0
    job_max = job.get("max_repeat", 0) if job.get("available") else 0
    job_total = job.get("total_posts", 0) if job.get("available") else 0
    if job_max:
        job_score = min(40, int((job_max ** 1.4) * 2))
    job_score += min(20, job_total // 2)

    # 점수 — 회사명 동시 등장 + 반복 채용이 가장 강력
    risk = min(
        100,
        n_company_match * 12 + n_targeted * 8 + sum_kw * 3 + job_score,
    )

    severity = "low"
    if n_company_match >= 2 or n_targeted >= 2 or job.get("severity") == "high":
        severity = "high"
    elif n_company_match >= 1 or n_match >= 2 or job.get("severity") == "medium":
        severity = "medium"

    return {
        "available": True,
        "query": query,
        "n_match": n_match,
        "n_company_match": n_company_match,
        "n_targeted": n_targeted,
        "risk": risk,
        "severity": severity,
        "fetched_in_ms": total_ms,
        "by_source": counts,
        "findings": deduped[:30],
        "job_postings": job,
        "presence": presence,
    }


class ExternalIn(BaseModel):
    query: str
    add_to_cluster: bool = True


@router.post("/scan")
def scan(inp: ExternalIn) -> dict:
    if not inp.query or len(inp.query.strip()) < 2:
        return {"available": False, "reason": "검색어 2자 이상"}
    result = _aggregate(inp.query.strip())
    if not (inp.add_to_cluster and result.get("available")):
        return result

    q = inp.query.strip()
    # 도메인별로 분리해서 신호 발사 — '회사명+체불 키워드 매칭'은 pay_default,
    # '회사명+채용 반복'은 hiring. 둘은 다른 도메인.
    if (result.get("n_company_match", 0) > 0 or result.get("n_targeted", 0) > 0):
        sev = "high" if (result.get("n_company_match", 0) >= 2 or result.get("n_targeted", 0) >= 2) else "medium"
        add_signal(
            company_raw=q, channel="external", domain="pay_default",
            severity=sev,
            source_ref=f"naver:체불키워드 회사명={result.get('n_company_match',0)} 타겟={result.get('n_targeted',0)}",
        )
    job = result.get("job_postings") or {}
    if job.get("severity") in ("medium", "high"):
        add_signal(
            company_raw=q, channel="external", domain="hiring",
            severity=job["severity"],
            source_ref=f"naver:채용 반복 max={job.get('max_repeat',0)} total={job.get('total_posts',0)}",
        )

    presence = result.get("presence") or {}
    if presence.get("suspicious") and presence.get("severity") in ("medium", "high"):
        add_signal(
            company_raw=q, channel="external", domain="closure",
            severity=presence["severity"],
            source_ref=f"presence:채용{presence.get('hiring',0)}/비채용{presence.get('non_hiring',0)} — 실체 약함",
        )
    return result


@router.get("/scan")
def scan_get(q: str) -> dict:
    return _aggregate(q)


# ──────────────────────────────────────────────
# 장소 정보 — 네이버 지역검색 + 구글 Places (리뷰)
# ──────────────────────────────────────────────

GOOGLE_FIND = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
GOOGLE_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"


def search_naver_local(query: str, display: int = 5) -> tuple[list[dict], int, int]:
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    csec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        return [], 0, 0
    t0 = time.time()
    try:
        r = requests.get(
            f"{NAVER_BASE}/local.json",
            params={"query": query, "display": display},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
            timeout=10,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("NAVER-local", f"{NAVER_BASE}/local.json", r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return [], r.status_code, dt
        items = r.json().get("items", []) or []
        # HTML 태그 제거
        for it in items:
            for k in ("title", "category", "address", "roadAddress"):
                if it.get(k):
                    it[k] = _strip_html(it[k])
        return items, 200, dt
    except requests.RequestException:
        dt = int((time.time() - t0) * 1000)
        log_call("NAVER-local", f"{NAVER_BASE}/local.json", 0, dt, False)
        return [], 0, dt


def google_find_place(query: str) -> tuple[dict | None, str | None, int]:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        return None, "키 미설정", 0
    t0 = time.time()
    try:
        r = requests.get(
            GOOGLE_FIND,
            params={
                "input": query,
                "inputtype": "textquery",
                "fields": "place_id,name,formatted_address,rating,user_ratings_total",
                "language": "ko",
                "region": "kr",
                "key": key,
            },
            timeout=10,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("GOOGLE-find", GOOGLE_FIND, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}", dt
        data = r.json()
        if data.get("status") not in ("OK",):
            return None, data.get("status") + ((" / " + data["error_message"]) if data.get("error_message") else ""), dt
        cands = data.get("candidates") or []
        if not cands:
            return None, "ZERO_RESULTS", dt
        return cands[0], None, dt
    except requests.RequestException as e:
        return None, str(e), 0


def google_place_details(place_id: str) -> tuple[dict | None, str | None, int]:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        return None, "키 미설정", 0
    t0 = time.time()
    try:
        r = requests.get(
            GOOGLE_DETAILS,
            params={
                "place_id": place_id,
                "fields": "name,formatted_address,rating,user_ratings_total,reviews,types,url",
                "language": "ko",
                "key": key,
            },
            timeout=10,
        )
        dt = int((time.time() - t0) * 1000)
        log_call("GOOGLE-details", GOOGLE_DETAILS, r.status_code, dt, r.status_code == 200)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}", dt
        data = r.json()
        if data.get("status") != "OK":
            return None, data.get("status"), dt
        return data.get("result") or {}, None, dt
    except requests.RequestException as e:
        return None, str(e), 0


@router.post("/place")
def place_scan(inp: ExternalIn) -> dict:
    q = inp.query.strip()
    if len(q) < 2:
        return {"available": False, "reason": "검색어 2자 이상"}

    # 네이버 지역검색
    naver_items, _, naver_ms = search_naver_local(q)
    naver_available = bool(os.environ.get("NAVER_CLIENT_ID")) and bool(os.environ.get("NAVER_CLIENT_SECRET"))

    # 구글 Places
    google_avail = bool(os.environ.get("GOOGLE_PLACES_API_KEY"))
    google_block: dict = {"available": google_avail, "reason": None}
    google_total_ms = 0

    if google_avail:
        cand, err, dt1 = google_find_place(q)
        google_total_ms += dt1
        google_block["error"] = err
        if cand:
            google_block["place_id"] = cand.get("place_id")
            google_block["name"] = cand.get("name")
            google_block["address"] = cand.get("formatted_address")
            google_block["rating"] = cand.get("rating")
            google_block["user_ratings_total"] = cand.get("user_ratings_total")

            details, derr, dt2 = google_place_details(cand["place_id"])
            google_total_ms += dt2
            google_block["details_error"] = derr
            if details:
                google_block["url"] = details.get("url")
                reviews = details.get("reviews") or []
                # 위험 키워드 매칭
                review_out: list[dict] = []
                for rv in reviews:
                    text = (rv.get("text") or "")
                    n, kws = _has_risk(text)
                    review_out.append({
                        "author": rv.get("author_name"),
                        "rating": rv.get("rating"),
                        "text": text[:280],
                        "time_desc": rv.get("relative_time_description"),
                        "matched_keywords": kws,
                        "match_count": n,
                    })
                google_block["reviews"] = review_out
                google_block["risk_match_count"] = sum(r["match_count"] for r in review_out)

    # 신호 발사 — 구글 리뷰에 위험 키워드가 있거나 별점이 매우 낮으면
    risk_n = google_block.get("risk_match_count", 0) if google_avail else 0
    rating = google_block.get("rating") if google_avail else None
    severity = None
    if risk_n >= 3 or (rating and rating <= 2.5):
        severity = "high"
    elif risk_n >= 1 or (rating and rating <= 3.5):
        severity = "medium"

    if inp.add_to_cluster and severity:
        # 평점·리뷰 부정 키워드는 reputation 도메인. 단, 리뷰에 임금체불 키워드가 직접 있으면 pay_default도.
        add_signal(
            company_raw=q, channel="external", domain="reputation",
            severity=severity,
            source_ref=f"google_reviews:rating={rating},risk_match={risk_n}",
        )
        if risk_n >= 1:
            add_signal(
                company_raw=q, channel="external", domain="pay_default",
                severity="medium" if risk_n < 3 else "high",
                source_ref=f"google_reviews_keyword:{risk_n}",
            )

    return {
        "query": q,
        "naver": {
            "available": naver_available,
            "items": naver_items,
            "fetched_in_ms": naver_ms,
        },
        "google": google_block,
        "google_total_ms": google_total_ms,
    }


@router.get("/place")
def place_scan_get(q: str) -> dict:
    return place_scan(ExternalIn(query=q, add_to_cluster=False))

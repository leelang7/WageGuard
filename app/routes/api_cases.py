"""W2 케이스 관리 — 임금체불 신고 → 접수 → 조사 → 처분 → 종결 워크플로"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import conn
from ..settings import ALLOWED_FILE_EXTS, CASE_FILES_DIR, MAX_FILE_BYTES
from .api_business import compute_risk, lookup_cell, search_defaulters
from .api_cluster import add_signal
from .api_notify import push_notification

router = APIRouter(prefix="/api/cases")

VALID_TRANSITIONS = {
    "received":      {"investigating", "dismissed"},
    "investigating": {"resolved", "dismissed"},
    "resolved":      set(),
    "dismissed":     set(),
}

STATUS_LABEL = {
    "received":      "접수",
    "investigating": "조사 중",
    "resolved":      "처분 완료",
    "dismissed":     "종결",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def gen_case_no() -> str:
    return f"P-{datetime.now():%Y%m%d}-{secrets.token_hex(2).upper()}"


class CaseIn(BaseModel):
    reporter_name: str
    reporter_contact: str
    is_anonymous: bool = False
    consent_personal: bool = False
    company: str
    company_addr: str | None = None
    company_bno: str | None = None
    company_tel: str | None = None
    incident_period: str | None = None
    amount_estimated: int = 0
    description: str | None = None


def _trust_score(company: str) -> dict:
    """동일 사업장에 대한 다중 신고자 × 다중 시점 누적 → 신뢰도 산출."""
    with conn() as c:
        rows = c.execute(
            """SELECT reporter_contact, submitter_ip, created_at
               FROM cases WHERE company = ?""",
            (company,),
        ).fetchall()
    n = len(rows)
    distinct_reporters = len({r["reporter_contact"] for r in rows if r["reporter_contact"]})
    distinct_ips = len({r["submitter_ip"] for r in rows if r["submitter_ip"]})
    # 시간 분산: 신고가 다른 날짜에 들어왔는지
    days = {(r["created_at"] or "")[:10] for r in rows}
    distinct_days = len(days - {""})
    trust = min(100, distinct_reporters * 30 + distinct_days * 15 + (n - 1) * 5)
    return {
        "n_reports": n,
        "distinct_reporters": distinct_reporters,
        "distinct_ips": distinct_ips,
        "distinct_days": distinct_days,
        "trust_score": trust,
    }


def _check_recent_duplicate(company: str, contact: str, ip: str) -> str | None:
    """24시간 내 동일 (회사+신고자) 중복 등록 차단."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    with conn() as c:
        same = c.execute(
            """SELECT case_no FROM cases
               WHERE company = ? AND created_at >= ?
                 AND (reporter_contact = ? OR submitter_ip = ?)
               LIMIT 1""",
            (company, cutoff, contact or "", ip or ""),
        ).fetchone()
    return same["case_no"] if same else None


def _sanitize_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "-_.()":
            keep.append(ch)
        else:
            keep.append("_")
    s = "".join(keep)
    return s[:80] if s else "file"


@router.post("")
async def file_case(
    request: Request,
    reporter_name: str = Form(...),
    reporter_contact: str = Form(...),
    is_anonymous: bool = Form(False),
    consent_personal: bool = Form(False),
    company: str = Form(...),
    company_addr: str | None = Form(None),
    company_bno: str | None = Form(None),
    company_tel: str | None = Form(None),
    incident_period: str | None = Form(None),
    amount_estimated: int = Form(0),
    description: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
) -> dict:
    if not consent_personal:
        raise HTTPException(400, "개인정보 수집·이용 동의가 필요합니다 (행정 처리에 사용되지 않으며 본인+감독관만 열람).")

    ip = request.client.host if request.client else ""
    dup = _check_recent_duplicate(company, reporter_contact, ip)
    if dup:
        raise HTTPException(409, f"동일 사업장에 대한 24시간 내 중복 신고가 있습니다 (사건 {dup}).")

    hits = search_defaulters(company)
    industry = hits[0]["industry"] if hits else None
    region = hits[0]["region"] if hits else None
    cell = lookup_cell(industry, region)
    risk = compute_risk(nts=None, defaulter_hits=hits, cell=cell)

    display_name = reporter_name if not is_anonymous else f"익명_{secrets.token_hex(2)}"
    case_no = gen_case_no()

    with conn() as conx:
        cur = conx.execute(
            """INSERT INTO cases
               (case_no, reporter_name, reporter_contact, is_anonymous, consent_personal,
                company, company_addr, company_bno, company_tel,
                incident_period, amount_estimated, description, risk_score, status,
                region, industry, submitter_ip, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                case_no, display_name, reporter_contact, int(is_anonymous), int(consent_personal),
                company, company_addr, company_bno, company_tel,
                incident_period, amount_estimated, description, risk["score"], "received",
                region, industry, ip, now(), now(),
            ),
        )
        case_id = cur.lastrowid
        conx.execute(
            "INSERT INTO case_events (case_id, actor, action, note, created_at) VALUES (?,?,?,?,?)",
            (case_id, display_name, "filed", "근로자가 임금체불 신고를 접수했습니다.", now()),
        )

        # 증빙 파일 저장
        case_dir = CASE_FILES_DIR / case_no
        case_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for uf in files or []:
            if not uf.filename:
                continue
            ext = Path(uf.filename).suffix.lower()
            if ext not in ALLOWED_FILE_EXTS:
                continue
            raw = await uf.read()
            if len(raw) > MAX_FILE_BYTES:
                continue
            sha = hashlib.sha256(raw).hexdigest()
            safe = _sanitize_filename(uf.filename)
            stored = case_dir / f"{secrets.token_hex(4)}_{safe}"
            stored.write_bytes(raw)
            conx.execute(
                """INSERT INTO case_files
                   (case_id, filename, stored_path, mime, bytes, sha256, uploaded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (case_id, uf.filename, str(stored.relative_to(CASE_FILES_DIR)),
                 uf.content_type or "", len(raw), sha, now()),
            )
            saved += 1

    trust = _trust_score(company)

    push_notification(
        audience="supervisor",
        severity="warning" if (risk["score"] >= 70 or trust["trust_score"] >= 60) else "info",
        title=f"신규 신고 {case_no} · 위험 {risk['score']} · 신뢰 {trust['trust_score']}",
        body=f"{company} ({region or '지역미상'}) — {display_name} 신고 (증빙 {saved}건, 누적 {trust['n_reports']}건)",
        link=f"/cases/{case_no}",
    )
    push_notification(
        audience="worker",
        severity="info",
        title=f"신고 접수 완료 · {case_no}",
        body=f"{company} 사건 접수. 진정서 양식은 사건 상세에서 출력 가능합니다.",
        link=f"/cases/{case_no}",
    )

    sig = add_signal(
        company_raw=company,
        channel="case",
        domain="pay_default",
        severity="high" if risk["score"] >= 70 or trust["trust_score"] >= 60 else "medium",
        source_ref=case_no,
        region=region,
        industry=industry,
    )

    return {
        "case_no": case_no, "id": case_id,
        "risk_score": risk["score"], "status": "received",
        "files_saved": saved,
        "trust": trust,
        "cluster": sig,
    }


@router.get("/{case_no}/files")
def list_files(case_no: str) -> list[dict]:
    with conn() as c:
        case = c.execute("SELECT id FROM cases WHERE case_no = ?", (case_no,)).fetchone()
        if not case:
            raise HTTPException(404, "case not found")
        rows = c.execute(
            "SELECT id, filename, mime, bytes, sha256, uploaded_at FROM case_files WHERE case_id = ?",
            (case["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{case_no}/files/{file_id}")
def download_file(case_no: str, file_id: int):
    with conn() as c:
        case = c.execute("SELECT id FROM cases WHERE case_no = ?", (case_no,)).fetchone()
        if not case:
            raise HTTPException(404, "case not found")
        f = c.execute(
            "SELECT filename, stored_path, mime FROM case_files WHERE id = ? AND case_id = ?",
            (file_id, case["id"]),
        ).fetchone()
    if not f:
        raise HTTPException(404, "file not found")
    path = CASE_FILES_DIR / f["stored_path"]
    if not path.exists():
        raise HTTPException(410, "file removed")
    return FileResponse(path, media_type=f["mime"] or "application/octet-stream", filename=f["filename"])


def _mask_company(name: str) -> str:
    """명예훼손 차단 — 회사명 일부 마스킹. 첫 글자 + ** + 마지막 1글자."""
    if not name:
        return "(미상)"
    s = str(name).strip()
    if len(s) <= 2:
        return s[0] + "*"
    return s[0] + "*" * max(1, len(s) - 2) + s[-1]


PUBLIC_TRUST_THRESHOLD = 60
PUBLIC_REPORTERS_THRESHOLD = 2


@router.get("/_public/by-company")
def public_by_company() -> list[dict]:
    """공개 집계 — 신뢰도·다중 신고자 통과한 사업장만 회사명 노출,
    그 외는 마스킹 (명예훼손·근로기준법 §43-2 외 공개 위험 차단)."""
    with conn() as c:
        rows = c.execute(
            """SELECT company, COUNT(*) as n,
                      MIN(created_at) as first_at, MAX(created_at) as last_at
               FROM cases GROUP BY company
               ORDER BY n DESC, last_at DESC"""
        ).fetchall()
    out = []
    for r in rows:
        ts = _trust_score(r["company"])
        verified = (
            ts["trust_score"] >= PUBLIC_TRUST_THRESHOLD
            or ts["distinct_reporters"] >= PUBLIC_REPORTERS_THRESHOLD
        )
        out.append({
            "company": r["company"] if verified else _mask_company(r["company"]),
            "company_full_visible": verified,
            "n_reports": r["n"],
            "first_at": r["first_at"],
            "last_at": r["last_at"],
            "trust_score": ts["trust_score"],
            "distinct_reporters": ts["distinct_reporters"],
            "publicly_listed": verified,
        })
    return out


@router.get("")
def list_cases(status: str | None = None, region: str | None = None) -> list[dict]:
    sql = "SELECT * FROM cases WHERE 1=1"
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if region:
        sql += " AND region = ?"
        args.append(region)
    sql += " ORDER BY (status='received') DESC, risk_score DESC, id DESC LIMIT 100"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


@router.get("/{case_no}")
def case_detail(case_no: str) -> dict:
    with conn() as c:
        row = c.execute("SELECT * FROM cases WHERE case_no = ?", (case_no,)).fetchone()
        if not row:
            raise HTTPException(404, "case not found")
        events = c.execute(
            "SELECT actor, action, note, created_at FROM case_events WHERE case_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
    return {"case": dict(row), "events": [dict(e) for e in events]}


class TransitionIn(BaseModel):
    to: str
    actor: str = "감독관"
    note: str | None = None


@router.post("/{case_no}/transition")
def transition(case_no: str, t: TransitionIn) -> dict:
    with conn() as c:
        row = c.execute("SELECT id, status, company, reporter_name FROM cases WHERE case_no = ?", (case_no,)).fetchone()
        if not row:
            raise HTTPException(404, "case not found")
        cur_status = row["status"]
        allowed = VALID_TRANSITIONS.get(cur_status, set())
        if t.to not in allowed:
            raise HTTPException(400, f"전이 불가: {cur_status} → {t.to} (허용: {sorted(allowed)})")

        c.execute("UPDATE cases SET status = ?, updated_at = ?, assigned_to = COALESCE(assigned_to, ?) WHERE id = ?",
                  (t.to, now(), t.actor, row["id"]))
        c.execute(
            "INSERT INTO case_events (case_id, actor, action, note, created_at) VALUES (?,?,?,?,?)",
            (row["id"], t.actor, f"{cur_status}->{t.to}", t.note, now()),
        )

    push_notification(
        audience="worker",
        severity="info",
        title=f"신고 케이스 상태 변경 · {case_no}",
        body=f"{STATUS_LABEL[cur_status]} → {STATUS_LABEL[t.to]}" + (f" / {t.note}" if t.note else ""),
        link=f"/cases/{case_no}",
    )
    return {"case_no": case_no, "from": cur_status, "to": t.to}


@router.get("/_stats/summary")
def summary() -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        recent = c.execute(
            "SELECT case_no, company, status, risk_score, created_at FROM cases ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "by_status": by_status,
        "total": sum(by_status.values()),
        "recent": [dict(r) for r in recent],
    }

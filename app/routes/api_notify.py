"""W4 알림 큐 — 이벤트 발생 시 사용자별 인박스에 적재"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from ..db import conn

router = APIRouter(prefix="/api/notifications")


def push_notification(audience: str, severity: str, title: str, body: str, link: str | None = None) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO notifications (audience, severity, title, body, link, read, created_at)
               VALUES (?,?,?,?,?,0,?)""",
            (audience, severity, title, body, link, datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


@router.get("")
def list_notifications(audience: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM notifications"
    args: list = []
    if audience:
        sql += " WHERE audience = ?"
        args.append(audience)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


@router.post("/{nid}/read")
def mark_read(nid: int) -> dict:
    with conn() as c:
        c.execute("UPDATE notifications SET read = 1 WHERE id = ?", (nid,))
    return {"id": nid, "read": True}


@router.get("/unread-count")
def unread_count(audience: str | None = None) -> dict:
    sql = "SELECT COUNT(*) FROM notifications WHERE read = 0"
    args: list = []
    if audience:
        sql += " AND audience = ?"
        args.append(audience)
    with conn() as c:
        n = c.execute(sql, args).fetchone()[0]
    return {"unread": n, "audience": audience}

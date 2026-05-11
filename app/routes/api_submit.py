"""제출 준비 체크리스트 — 마감 D-day 기준 진행 상태."""
from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..db import conn
from ..settings import ROOT

router = APIRouter(prefix="/api/submit")


@router.get("/business-plan.md")
def dl_business_plan():
    return FileResponse(str(ROOT / "proposal" / "business_plan.md"),
                        media_type="text/markdown",
                        filename="WageGuard_사업계획서.md")


@router.get("/data-spec-form.md")
def dl_data_spec():
    return FileResponse(str(ROOT / "proposal" / "data_spec_form.md"),
                        media_type="text/markdown",
                        filename="WageGuard_데이터명세서.md")


@router.get("/video-script.md")
def dl_video():
    return FileResponse(str(ROOT / "proposal" / "video_script.md"),
                        media_type="text/markdown",
                        filename="WageGuard_동영상스크립트.md")

DEADLINE = date(2026, 5, 14)


def _file_exists(rel: str) -> bool:
    return (ROOT / rel).exists()


@router.get("/checklist")
def checklist() -> dict:
    """출품 제출 준비 항목 + 진행 상태."""
    today = date.today()
    days_left = (DEADLINE - today).days

    # 환경변수 발급 카운트
    work_keys = sum(1 for k in (
        "WORK24_AUTH_KEY_JOB", "WORK24_AUTH_KEY_DUTY",
        "WORK24_AUTH_KEY_TRAINING", "WORK24_AUTH_KEY_CAREER"
    ) if os.getenv(k))
    has_dgk = bool(os.getenv("DATA_GO_KR_KEY"))
    has_dart = bool(os.getenv("OPENDART_KEY"))

    # DB 점검 적재 카운트 (시스템 라이브 증거)
    with conn() as c:
        try:
            n_events = c.execute("SELECT COUNT(*) AS n FROM system_events").fetchone()["n"]
            n_insp = c.execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"]
        except Exception:
            n_events = n_insp = 0

    items = [
        # 자료 (시스템에 이미 있음)
        {
            "section": "📄 출품 자료",
            "name": "사업계획서",
            "detail": "proposal/business_plan.md (A4 10p)",
            "done": _file_exists("proposal/business_plan.md"),
            "auto": True,
        },
        {
            "section": "📄 출품 자료",
            "name": "데이터 명세서 (누리집 입력용)",
            "detail": "proposal/data_spec_form.md (입력 #1~#17)",
            "done": _file_exists("proposal/data_spec_form.md"),
            "auto": True,
        },
        {
            "section": "📄 출품 자료",
            "name": "1페이지 요약 (인쇄용)",
            "detail": "/onepager 라이브 + 인쇄",
            "done": _file_exists("app/templates/onepager.html"),
            "auto": True,
        },
        {
            "section": "📄 출품 자료",
            "name": "동영상 시연 스크립트",
            "detail": "proposal/video_script.md (5분 7컷)",
            "done": _file_exists("proposal/video_script.md"),
            "auto": True,
        },

        # 인증 (사용자가 발급함)
        {
            "section": "🔑 인증 발급",
            "name": "data.go.kr 인증키",
            "detail": "DATA_GO_KR_KEY",
            "done": has_dgk,
            "auto": True,
        },
        {
            "section": "🔑 인증 발급",
            "name": "한국장애인고용공단 KEAD 3개",
            "detail": "근로지원인 구인·수행기관·고용개발원 보고서",
            "done": has_dgk,
            "auto": True,
        },
        {
            "section": "🔑 인증 발급",
            "name": "한국고용정보원 워크넷 4개 authKey",
            "detail": "직업·직무·훈련·취업역량",
            "done": work_keys >= 4,
            "auto": True,
            "value": f"{work_keys}/4",
        },
        {
            "section": "🔑 인증 발급",
            "name": "금융감독원 DART API 키 (OPENDART_KEY)",
            "detail": "opendart.fss.or.kr 회원가입 → 키 발급 → .env OPENDART_KEY 등록",
            "done": has_dart,
            "auto": True,
            "value": "✓" if has_dart else "미설정",
        },

        # 시스템 동작 증거
        {
            "section": "⚙ 시스템 라이브 증거",
            "name": "운영 콘솔 이벤트 적재",
            "detail": f"system_events 테이블 {n_events}건",
            "done": n_events > 0,
            "auto": True,
            "value": str(n_events),
        },
        {
            "section": "⚙ 시스템 라이브 증거",
            "name": "점검 사이클 DB 적재",
            "detail": f"inspections 테이블 {n_insp}건",
            "done": True,  # 0이어도 OK (운영 시뮬에서 입력)
            "auto": True,
            "value": str(n_insp),
        },

        # 사용자 직접 액션 필요
        {
            "section": "🚀 본인 직접 액션",
            "name": "GitHub 저장소 생성 + 푸시",
            "detail": "github.com/<user>/WageGuard",
            "done": False,
            "auto": False,
        },
        {
            "section": "🚀 본인 직접 액션",
            "name": "Render.com 라이브 호스팅",
            "detail": "WageGuard.onrender.com (시제품 구체화 = 발표 자격 핵심)",
            "done": False,
            "auto": False,
        },
        {
            "section": "🚀 본인 직접 액션",
            "name": "동영상 5분 녹화 (1080p mp4)",
            "detail": "OBS Studio · 1차 통과 후 (6/17~) 제출",
            "done": False,
            "auto": False,
        },
        {
            "section": "🚀 본인 직접 액션",
            "name": "공통서류 4종 PDF 스캔",
            "detail": "참가신청서·개인정보 동의·저작권 동의·청렴 서약",
            "done": False,
            "auto": False,
        },
        {
            "section": "🚀 본인 직접 액션",
            "name": "누리집 데이터명세서 입력",
            "detail": "data_spec_form.md 17개 항목 그대로 복붙",
            "done": False,
            "auto": False,
        },
    ]

    # 섹션별 진행률
    sections: dict[str, dict] = {}
    for it in items:
        s = it["section"]
        if s not in sections:
            sections[s] = {"name": s, "total": 0, "done": 0, "items": []}
        sections[s]["total"] += 1
        if it["done"]:
            sections[s]["done"] += 1
        sections[s]["items"].append(it)

    # 종합 진행률
    auto_total = sum(1 for it in items if it["auto"])
    auto_done = sum(1 for it in items if it["auto"] and it["done"])
    user_total = sum(1 for it in items if not it["auto"])
    user_done = sum(1 for it in items if not it["auto"] and it["done"])

    return {
        "deadline": DEADLINE.isoformat(),
        "today": today.isoformat(),
        "days_left": days_left,
        "auto_progress": f"{auto_done}/{auto_total}",
        "user_progress": f"{user_done}/{user_total}",
        "auto_pct": round(auto_done / max(auto_total, 1) * 100, 1),
        "user_pct": round(user_done / max(user_total, 1) * 100, 1),
        "sections": list(sections.values()),
        "total_items": len(items),
        "all_items": items,
    }

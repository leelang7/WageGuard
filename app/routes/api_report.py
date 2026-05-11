"""W5 임금체불 진정서 자동생성"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from ..db import conn

# 모듈 로드 시 1회 한국어 폰트 등록 시도
_KFONT = "Helvetica"
try:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for _p in [
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
    ]:
        if Path(_p).exists():
            try:
                pdfmetrics.registerFont(TTFont("KFont", _p))
                _KFONT = "KFont"
                break
            except Exception:
                continue
except Exception:
    pass

router = APIRouter(prefix="/api/report")


REPORT_TEMPLATE = """\
임금체불 진정서

수신: 고용노동부 ○○지방고용노동청 (또는 1350 종합상담센터 안내)
일자: {today}
사건번호(접수): {case_no}

────────────────────────────────────────────────────
1. 진정인 (근로자)
   성명         : {reporter_name}
   연락처       : {reporter_contact}

2. 피진정인 (사업주)
   사업장명     : {company}
   사업장 주소  : {company_addr}
   업종         : {industry}
   지역         : {region}

3. 체불 발생 사실
   기간         : {incident_period}
   체불 추정액  : {amount} 원
   상세         :
{description}

4. 시스템 위험점수 ({risk_score}/100)
   본 진정 사건은 WageGuard 사전 탐지 시스템에서
   다음 신호를 결합해 위험점수를 산출하였습니다.
{factors}

5. 첨부 (제출 시 함께 준비할 자료)
   □ 근로계약서 사본
   □ 임금명세서 (최근 3개월)
   □ 통장 거래내역 (월급 입금 기록)
   □ 출퇴근 기록 / 업무지시 메시지
   □ 기타 임금체불을 입증할 수 있는 자료

위와 같이 임금체불 사실을 진정합니다.
{today}

진정인 {reporter_name} (서명 / 인)
"""


def _format_factors(score: int) -> str:
    return f"   • 종합 위험점수: {score}점 (시스템 자동 산출)"


@router.get("/{case_no}", response_class=PlainTextResponse)
def report_text(case_no: str) -> str:
    with conn() as c:
        row = c.execute("SELECT * FROM cases WHERE case_no = ?", (case_no,)).fetchone()
    if not row:
        raise HTTPException(404, "case not found")

    return REPORT_TEMPLATE.format(
        today=datetime.now().strftime("%Y. %m. %d."),
        case_no=row["case_no"],
        reporter_name=row["reporter_name"] or "-",
        reporter_contact=row["reporter_contact"] or "-",
        company=row["company"] or "-",
        company_addr=row["company_addr"] or "(상세주소 미기재)",
        industry=row["industry"] or "(업종 미상)",
        region=row["region"] or "(지역 미상)",
        incident_period=row["incident_period"] or "(기재 필요)",
        amount=f"{row['amount_estimated']:,}" if row["amount_estimated"] else "(금액 산정 필요)",
        description="\n".join("   " + l for l in (row["description"] or "(상세 사유 기재 필요)").splitlines()),
        risk_score=row["risk_score"] or 0,
        factors=_format_factors(row["risk_score"] or 0),
    )


@router.get("/{case_no}/pdf")
def report_pdf(case_no: str):
    """진정서 PDF 직접 다운로드 — reportlab. 한글 폰트는 시스템 NanumGothic·Malgun 시도."""
    text = report_text(case_no)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=22 * mm, rightMargin=22 * mm,
                                topMargin=22 * mm, bottomMargin=22 * mm,
                                title=f"진정서 {case_no}")
        body = ParagraphStyle(
            "kbody", fontName=_KFONT, fontSize=10, leading=15,
        )
        flow = []
        for line in text.split("\n"):
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if not safe.strip():
                flow.append(Spacer(1, 6))
                continue
            # 들여쓰기 보존
            if line.startswith("   "):
                safe = "&nbsp;&nbsp;&nbsp;" + safe.lstrip()
            flow.append(Paragraph(safe, body))
        doc.build(flow)
        buf.seek(0)
        # 한글 파일명은 RFC 5987 형식으로
        from urllib.parse import quote
        fname = quote(f"진정서_{case_no}.pdf")
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"PDF 생성 실패: {e}")


@router.get("/{case_no}/html", response_class=HTMLResponse)
def report_html(case_no: str) -> str:
    text = report_text(case_no)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>진정서 {case_no}</title>
<style>
@page {{ size: A4; margin: 22mm; }}
body {{ font-family: 'Pretendard', 'Malgun Gothic', sans-serif; max-width: 720px; margin: 30px auto; padding: 30px; }}
pre {{ white-space: pre-wrap; font-family: 'Pretendard', 'Malgun Gothic', sans-serif; font-size: 14px; line-height: 1.7; }}
.print-bar {{ position: sticky; top: 0; background: #fff; border-bottom: 1px solid #eee; padding: 8px 0; margin: -30px -30px 20px; padding-left: 30px; }}
@media print {{ .print-bar {{ display: none; }} }}
button {{ background: #0f172a; color: white; border: 0; padding: 6px 14px; border-radius: 4px; cursor: pointer; }}
</style></head><body>
<div class="print-bar"><button onclick="window.print()">🖨 인쇄 / PDF 저장</button>
  <span style="color:#64748b;margin-left:10px;font-size:12px">본 양식은 시스템이 자동 생성한 초안입니다. 제출 전 검토·서명하세요.</span>
</div>
<pre>{text}</pre>
</body></html>"""

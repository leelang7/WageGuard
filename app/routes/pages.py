from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..settings import APP_NAME, APP_TAGLINE, TEMPLATES

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES))


def ctx(active: str, **extra) -> dict:
    from ..settings import APP_OPERATOR, APP_USERS
    return {
        "app_name": APP_NAME,
        "app_tagline": APP_TAGLINE,
        "app_operator": APP_OPERATOR,
        "app_users": APP_USERS,
        "active": active,
        **extra,
    }


def render(request: Request, name: str, active: str, **extra):
    return templates.TemplateResponse(request, name, ctx(active, **extra))


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render(request, "home.html", "home")


@router.get("/industry", response_class=HTMLResponse)
def industry(request: Request):
    return render(request, "industry.html", "industry")


@router.get("/region", response_class=HTMLResponse)
def region(request: Request):
    return render(request, "region.html", "region")


@router.get("/business", response_class=HTMLResponse)
def business(request: Request):
    return render(request, "business.html", "business")


@router.get("/graph", response_class=HTMLResponse)
def graph(request: Request):
    return render(request, "graph.html", "graph")


@router.get("/selfcheck", response_class=HTMLResponse)
def selfcheck(request: Request):
    return render(request, "selfcheck.html", "selfcheck")


@router.get("/watch", response_class=HTMLResponse)
def watch(request: Request):
    return render(request, "watch.html", "watch")


@router.get("/supervisor", response_class=HTMLResponse)
def supervisor(request: Request):
    return render(request, "supervisor.html", "supervisor")


@router.get("/cases", response_class=HTMLResponse)
def cases(request: Request):
    return render(request, "cases.html", "cases")


@router.get("/신고", response_class=HTMLResponse)
def report_new(request: Request):
    return render(request, "report.html", "report")


@router.get("/owner", response_class=HTMLResponse)
def owner(request: Request):
    return render(request, "owner.html", "owner")


@router.get("/notifications", response_class=HTMLResponse)
def notify(request: Request):
    return render(request, "notify.html", "notify")


@router.get("/cluster", response_class=HTMLResponse)
def cluster(request: Request):
    return render(request, "cluster.html", "cluster")


@router.get("/reports", response_class=HTMLResponse)
def reports_public(request: Request):
    return render(request, "reports_public.html", "reports")


@router.get("/metrics", response_class=HTMLResponse)
def metrics(request: Request):
    return render(request, "metrics.html", "metrics")


@router.get("/wage-calc", response_class=HTMLResponse)
def wage_calc(request: Request):
    return render(request, "wage_calc.html", "wage_calc")


@router.get("/intel", response_class=HTMLResponse)
def intel(request: Request):
    return render(request, "intel.html", "intel")


@router.get("/payslip-check", response_class=HTMLResponse)
def payslip_check(request: Request):
    return render(request, "payslip_check.html", "payslip_check")


@router.get("/company/{name:path}", response_class=HTMLResponse)
def company_profile(request: Request, name: str):
    return render(request, "company.html", "company", company_name=name)


@router.get("/demo", response_class=HTMLResponse)
def demo(request: Request):
    return render(request, "demo.html", "demo")


@router.get("/attest", response_class=HTMLResponse)
def attest(request: Request):
    return render(request, "attest.html", "attest")


@router.get("/owner-notice", response_class=HTMLResponse)
def owner_notice(request: Request):
    return render(request, "owner_notice.html", "owner_notice")


@router.get("/evidence", response_class=HTMLResponse)
def evidence(request: Request):
    return render(request, "evidence.html", "evidence")


@router.get("/checkin", response_class=HTMLResponse)
def checkin(request: Request):
    return render(request, "checkin.html", "checkin")


@router.get("/incentives", response_class=HTMLResponse)
def incentives(request: Request):
    return render(request, "incentives.html", "incentives")


@router.get("/me", response_class=HTMLResponse)
def me(request: Request):
    return render(request, "me.html", "me")


@router.get("/kakao", response_class=HTMLResponse)
def kakao(request: Request):
    return render(request, "kakao.html", "kakao")


@router.get("/onepager", response_class=HTMLResponse)
def onepager(request: Request):
    from fastapi.responses import FileResponse
    from ..settings import TEMPLATES
    return FileResponse(str(TEMPLATES / "onepager.html"), media_type="text/html")


@router.get("/owner-notice/{company:path}", response_class=HTMLResponse)
def owner_notice_company(request: Request, company: str):
    return render(request, "owner_notice.html", "owner_notice", prefill_company=company)


@router.get("/m6", response_class=HTMLResponse)
def m6(request: Request):
    return render(request, "m6.html", "m6")


@router.get("/m6/embed-demo", response_class=HTMLResponse)
def m6_embed_demo(request: Request):
    return render(request, "embed_demo.html", "m6")


@router.post("/m6/embed-demo/submit", response_class=HTMLResponse)
def m6_embed_demo_submit(request: Request):
    return HTMLResponse(
        "<html><body style='font-family:Pretendard,sans-serif;padding:40px;background:#f8fafc'>"
        "<div style='max-width:600px;margin:0 auto;background:white;padding:30px;border-radius:16px;border:1px solid #e2e8f0'>"
        "<h2>✅ 신청 정상 처리 (가상)</h2>"
        "<p style='color:#64748b'>SDK 점수 임계 미만 — 정상 신청 흐름으로 라우팅됨.</p>"
        "<a href='/m6/embed-demo' style='color:#4338ca'>← 데모로 돌아가기</a>"
        "</div></body></html>"
    )


@router.get("/ml", response_class=HTMLResponse)
def ml_page(request: Request):
    return render(request, "ml.html", "ml")


@router.get("/scalability", response_class=HTMLResponse)
def scalability_page(request: Request):
    return render(request, "scalability.html", "scalability")


@router.get("/disability", response_class=HTMLResponse)
def disability_page(request: Request):
    return render(request, "disability.html", "disability")


@router.get("/match", response_class=HTMLResponse)
def match_page(request: Request):
    return render(request, "match.html", "match")


@router.get("/triage", response_class=HTMLResponse)
def triage_page(request: Request):
    return render(request, "triage.html", "triage")


@router.get("/scenario", response_class=HTMLResponse)
def scenario_page(request: Request):
    return render(request, "scenario.html", "scenario")


@router.get("/judge", response_class=HTMLResponse)
def judge_guide(request: Request):
    return render(request, "judge_guide.html", "judge")


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return render(request, "audit.html", "audit")


@router.get("/operator", response_class=HTMLResponse)
def operator_page(request: Request):
    return render(request, "operator.html", "operator")


@router.get("/ops", response_class=HTMLResponse)
def ops_page(request: Request):
    return render(request, "ops.html", "ops")


@router.get("/submit", response_class=HTMLResponse)
def submit_page(request: Request):
    return render(request, "submit.html", "submit")


@router.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request):
    return render(request, "verify.html", "verify")


@router.get("/health", response_class=HTMLResponse)
def health(request: Request):
    return render(request, "health.html", "health")


@router.get("/dart", response_class=HTMLResponse)
def dart_page(request: Request):
    return render(request, "dart.html", "dart")


@router.get("/insurance-cross", response_class=HTMLResponse)
def insurance_cross_page(request: Request):
    return render(request, "insurance_cross.html", "insurance_cross")


@router.get("/pension", response_class=HTMLResponse)
def pension_page(request: Request):
    return render(request, "pension.html", "pension")


@router.get("/rdp-expansion", response_class=HTMLResponse)
def rdp_expansion_page(request: Request):
    return render(request, "rdp_expansion.html", "rdp_expansion")

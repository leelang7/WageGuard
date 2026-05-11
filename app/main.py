from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .ingest import ingest_defaulters, ingest_risk_cells
from . import scheduler
from .routes import (
    pages, api_stats, api_business, api_macro, api_m6, api_health,
    api_graph, api_selfcheck, api_watch, api_supervisor,
    api_cases, api_owner, api_notify, api_report, api_cluster,
    api_external, api_pension, api_metrics, api_wage,
    api_predict, api_intel, api_payslip, api_company,
    api_blacklist, api_attest, api_swarm, api_owner_notice, api_evidence,
    api_checkin, api_me, api_ml, api_kead, api_match, api_triage, api_status,
    api_worknet, api_audit, api_operator, api_sdk_weights, api_ops, api_submit,
    api_verify, api_dart, api_insurance_cross,
)
from .events import log_event
from .middleware import MetricsMiddleware
from .settings import APP_NAME, STATIC


def create_app() -> FastAPI:
    init_db()
    if ingest_defaulters() == 0:
        pass  # 비어있어도 서버는 뜸
    if ingest_risk_cells() == 0:
        pass

    app = FastAPI(title=APP_NAME)
    app.add_middleware(MetricsMiddleware)

    STATIC.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    app.include_router(pages.router)
    app.include_router(api_stats.router)
    app.include_router(api_business.router)
    app.include_router(api_macro.router)
    app.include_router(api_m6.router)
    app.include_router(api_graph.router)
    app.include_router(api_selfcheck.router)
    app.include_router(api_watch.router)
    app.include_router(api_supervisor.router)
    app.include_router(api_cases.router)
    app.include_router(api_owner.router)
    app.include_router(api_notify.router)
    app.include_router(api_report.router)
    app.include_router(api_cluster.router)
    app.include_router(api_external.router)
    app.include_router(api_pension.router)
    app.include_router(api_metrics.router)
    app.include_router(api_wage.router)
    app.include_router(api_predict.router)
    app.include_router(api_intel.router)
    app.include_router(api_payslip.router)
    app.include_router(api_company.router)
    app.include_router(api_blacklist.router)
    app.include_router(api_attest.router)
    app.include_router(api_swarm.router)
    app.include_router(api_owner_notice.router)
    app.include_router(api_evidence.router)
    app.include_router(api_checkin.router)
    app.include_router(api_me.router)
    app.include_router(api_ml.router)
    app.include_router(api_kead.router)
    app.include_router(api_match.router)
    app.include_router(api_triage.router)
    app.include_router(api_status.router)
    app.include_router(api_worknet.router)
    app.include_router(api_audit.router)
    app.include_router(api_operator.router)
    app.include_router(api_sdk_weights.router)
    app.include_router(api_ops.router)
    app.include_router(api_submit.router)
    app.include_router(api_verify.router)
    app.include_router(api_dart.router)
    app.include_router(api_insurance_cross.router)
    app.include_router(api_health.router)

    log_event("startup", "FastAPI 앱 부팅 완료", actor="system",
              payload={"name": APP_NAME})

    scheduler.start()
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)

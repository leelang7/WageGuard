from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .settings import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS defaulters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round TEXT, name TEXT, age INTEGER,
    company TEXT, industry TEXT,
    owner_addr TEXT, company_addr TEXT, region TEXT,
    amount INTEGER, year INTEGER
);
CREATE INDEX IF NOT EXISTS idx_def_industry ON defaulters(industry);
CREATE INDEX IF NOT EXISTS idx_def_region ON defaulters(region);
CREATE INDEX IF NOT EXISTS idx_def_year ON defaulters(year);

CREATE TABLE IF NOT EXISTS risk_cells (
    industry TEXT, region TEXT,
    risk_score REAL, count INTEGER, avg_amt INTEGER,
    prev_2y INTEGER, recent_2y INTEGER, trend REAL,
    s1_count REAL, s2_amt REAL, s3_trend REAL,
    PRIMARY KEY (industry, region)
);

CREATE TABLE IF NOT EXISTS business_cache (
    bno TEXT PRIMARY KEY,
    nts_payload TEXT,
    kcomwel_payload TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS macro_eis (
    region TEXT, year_month TEXT, kind TEXT, payload TEXT,
    fetched_at TEXT,
    PRIMARY KEY (region, year_month, kind)
);

CREATE TABLE IF NOT EXISTS m6_logs (
    apply_id TEXT PRIMARY KEY,
    payload TEXT, score INTEGER, pred INTEGER, label INTEGER,
    phase INTEGER, created_at TEXT
);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api TEXT, endpoint TEXT, status INTEGER,
    duration_ms INTEGER, ok INTEGER,
    called_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    bno TEXT,
    company_query TEXT,
    last_status TEXT,
    last_score INTEGER,
    last_checked_at TEXT,
    created_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER,
    event_type TEXT,
    detail TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT UNIQUE,
    reporter_name TEXT,
    reporter_contact TEXT,
    is_anonymous INTEGER DEFAULT 0,
    consent_personal INTEGER DEFAULT 0,
    company TEXT,
    company_addr TEXT,
    company_bno TEXT,
    company_tel TEXT,
    incident_period TEXT,
    amount_estimated INTEGER,
    description TEXT,
    risk_score INTEGER,
    status TEXT,            -- received / investigating / resolved / dismissed
    assigned_to TEXT,
    region TEXT,
    industry TEXT,
    submitter_ip TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_region ON cases(region);

CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    actor TEXT,
    action TEXT,
    note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audience TEXT,          -- worker / owner / supervisor / system
    severity TEXT,          -- info / warning / critical
    title TEXT,
    body TEXT,
    link TEXT,
    read INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS company_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_norm TEXT,
    company_raw TEXT,
    channel TEXT,           -- case / watch / selfcheck / diagnosis
    severity TEXT,           -- low / medium / high
    source_ref TEXT,
    region TEXT,
    industry TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sig_company ON company_signals(company_norm);

CREATE TABLE IF NOT EXISTS clusters_alerted (
    company_norm TEXT PRIMARY KEY,
    last_alert_n INTEGER,
    last_alert_at TEXT
);

CREATE TABLE IF NOT EXISTS owner_attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, company_norm TEXT,
    bzowr_rgst_no TEXT,
    representative TEXT,
    contact TEXT,
    period_ym TEXT,         -- YYYY-MM (이행 기간)
    employee_count INTEGER,
    payment_date TEXT,      -- 약속한 임금 지급일 YYYY-MM-DD
    paid_total INTEGER,     -- 그 달 지급 총액
    insured_count INTEGER,  -- 4대보험 가입자수 자가신고
    payslip_issued INTEGER, -- 명세서 교부 여부
    avg_hours REAL,         -- 평균 근로시간
    sha256 TEXT,
    consent INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attest_norm ON owner_attestations(company_norm);

CREATE TABLE IF NOT EXISTS owner_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, company_norm TEXT,
    bzowr_rgst_no TEXT,
    owner_name TEXT,
    owner_contact TEXT,
    verified INTEGER DEFAULT 0,
    last_alerted_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_osub_norm ON owner_subscriptions(company_norm);

CREATE TABLE IF NOT EXISTS owner_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, company_norm TEXT,
    response_text TEXT,
    evidence_path TEXT,
    accepted INTEGER DEFAULT 0,
    reviewed_by TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS worker_checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, company_norm TEXT,
    worker_alias TEXT,
    contact_hash TEXT,         -- 연락처 해시 (중복 차단용 익명)
    period_ym TEXT,            -- YYYY-MM
    status TEXT,               -- received / late / partial / unpaid
    paid_amount INTEGER,
    paid_date TEXT,
    expected_date TEXT,
    note TEXT,
    submitter_ip TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chk_norm ON worker_checkins(company_norm);
CREATE INDEX IF NOT EXISTS idx_chk_period ON worker_checkins(period_ym);

CREATE TABLE IF NOT EXISTS pay_promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, company_norm TEXT,
    promised_date TEXT,
    note TEXT,
    fulfilled INTEGER DEFAULT 0,
    fulfilled_at TEXT,
    violation_logged INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_promise_norm ON pay_promises(company_norm);

CREATE TABLE IF NOT EXISTS case_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    filename TEXT,
    stored_path TEXT,
    mime TEXT,
    bytes INTEGER,
    sha256 TEXT,
    uploaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_case_files_case ON case_files(case_id);

CREATE TABLE IF NOT EXISTS nps_workplaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wkpl_nm TEXT,
    wkpl_nm_norm TEXT,
    bzowr_rgst_no TEXT,
    addr TEXT,
    region_dg TEXT,
    region_sgg TEXT,
    region_emd TEXT,
    industry TEXT,
    subscriber_cnt INTEGER,
    new_cnt INTEGER,
    lost_cnt INTEGER,
    avg_pay INTEGER,
    adpt_dt TEXT,
    snapshot_ym TEXT
);
CREATE INDEX IF NOT EXISTS idx_nps_norm ON nps_workplaces(wkpl_nm_norm);
CREATE INDEX IF NOT EXISTS idx_nps_bno ON nps_workplaces(bzowr_rgst_no);

CREATE TABLE IF NOT EXISTS dart_corps (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT,
    corp_name_norm TEXT,
    stock_code TEXT,
    corp_cls TEXT,
    modify_date TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dart_corps_name ON dart_corps(corp_name_norm);
CREATE INDEX IF NOT EXISTS idx_dart_corps_stock ON dart_corps(stock_code);

CREATE TABLE IF NOT EXISTS dart_financial_risks (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT,
    stock_code TEXT,
    year INTEGER,
    risk_score INTEGER,
    signals TEXT,
    financials TEXT,
    source TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dart_risk_score ON dart_financial_risks(risk_score DESC);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,         -- heartbeat / ingest / model_train / inspection / api_call / startup
    severity TEXT,     -- info / warn / error
    actor TEXT,        -- system / scheduler / operator / user
    summary TEXT,
    payload TEXT,      -- JSON
    duration_ms INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sysev_created ON system_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sysev_kind ON system_events(kind);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER,
    business_name TEXT,
    verdict TEXT,         -- violation / clean
    signals TEXT,         -- JSON array
    suspicion_score REAL,
    inspector TEXT,       -- 'simulator' / 'kead' / 'supervisor'
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_insp_created ON inspections(created_at DESC);

CREATE TABLE IF NOT EXISTS m6_admin_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT,                -- 익명화 수급자 ID
    prev_bno TEXT,                 -- 이전 직장 사업자번호
    separation_reason TEXT,        -- 이직사유: voluntary/involuntary/contract/unknown
    insurance_loss_date TEXT,      -- 고용보험 상실신고일 YYYY-MM-DD
    apply_date TEXT,               -- 실업급여 신청일 YYYY-MM-DD
    loss_retroactive_days INTEGER, -- 상실신고 소급일수 (음수=역순)
    prev_company_in_defaulter INTEGER DEFAULT 0,  -- 이전사업장 체불명단 등재
    prev_company_defaulter_amt INTEGER DEFAULT 0, -- 체불금액
    training_ip_country TEXT,      -- 최근 훈련수강 IP 국가
    training_ip_matches_apply INTEGER DEFAULT 1,  -- 훈련IP = 신청IP 여부
    region_benefit_surge_pct REAL DEFAULT 0,      -- 해당지역 실업급여 전월대비 증감
    baseline_device_fp TEXT,       -- 이전 신청 디바이스 지문
    baseline_tz_offset INTEGER,    -- 이전 신청 timezone offset
    apply_count_6m INTEGER DEFAULT 0,             -- 6개월 내 신청 횟수
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_m6adm_user ON m6_admin_signals(user_hash);
CREATE INDEX IF NOT EXISTS idx_m6adm_bno ON m6_admin_signals(prev_bno);
"""


MIGRATIONS = [
    "ALTER TABLE company_signals ADD COLUMN domain TEXT",
    "ALTER TABLE company_signals ADD COLUMN event_at TEXT",
    "ALTER TABLE cases ADD COLUMN is_anonymous INTEGER DEFAULT 0",
    "ALTER TABLE cases ADD COLUMN consent_personal INTEGER DEFAULT 0",
    "ALTER TABLE cases ADD COLUMN company_bno TEXT",
    "ALTER TABLE cases ADD COLUMN company_tel TEXT",
    "ALTER TABLE cases ADD COLUMN submitter_ip TEXT",
    # Phase 2.5 행정 신호 로그 확장
    "ALTER TABLE m6_logs ADD COLUMN phase25_score INTEGER DEFAULT 0",
    "ALTER TABLE m6_logs ADD COLUMN admin_signals TEXT",  # JSON: L5 신호 목록
]


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                c.execute(sql)
            except Exception:
                pass  # column already exists


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

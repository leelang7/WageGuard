# WageGuard — 임금체불 위험 사업장 점검 우선순위 AI

8기관 공공데이터 교차 분석으로 전국 사업장의 임금체불 위험도를 자동 정렬합니다.  
장애인 근로자 우선 보호 · 체불 3~6개월 전 선행 탐지 · 부정수급 실시간 차단 SDK.

---

## 한 줄 정의

매칭은 워크넷이, 적발은 운영주체가, 우선순위는 **WageGuard**가 정렬합니다.

---

## 즉시 실행

```bash
# Windows
run.bat

# PowerShell
run.ps1

# Git Bash / WSL
bash run.sh
```

브라우저: **http://127.0.0.1:8123**

---

## 주요 화면

| 경로 | 설명 |
|---|---|
| `/` | 전국 대시보드 — 고위험 TOP 5, KPI, 지역·업종 차트 |
| `/triage` | 점검 우선순위 — 8기관 교차 의심도 전체 목록 |
| `/verify` | LIVE 7-step SSE 실시간 검증 |
| `/disability` | KEAD·고용정보원 데이터 결합 — 장애인 근로자 우선 보호 |
| `/ml` | AI 모델 — Logistic Regression 가중치·K-fold CV·Ablation |
| `/dart` | DART 재무위험 선행지표 |
| `/pension` | NPS 가입자 이탈 Z-score 경보 |
| `/insurance-cross` | 4대보험 삼각검증 |
| `/scenario` | 4개 사례 워크스루 |
| `/m6` | 부정수급 차단 SDK — 9 신호 라이브 측정 |
| `/reports` | 공개 신고 집계 |
| `/evidence` | 몬테카를로 사회적 임팩트 근거 |
| `/operator` | 운영주체(점검관) 시뮬레이터 |
| `/신고` | 익명 체불 신고 접수 |

---

## 아키텍처

```
FastAPI + SQLite + Jinja2 + Tailwind CSS + ECharts + htmx
```

- **Track A** — 부정수급 실시간 차단 SDK (`/m6`)  
  브라우저 9 신호(timezone·언어·WebRTC·WebGL·마우스·키 등)로 RDP 원격 접속 탐지  
  Phase 1 F1 **0.864** (1,000건 시뮬, 브라우저 신호)

- **Track B** — 임금체불 위험 사업장 점검 우선순위 정렬 (`/triage`)  
  Logistic Regression 9특성 · K-fold CV F1 **0.928** · Holdout F1 **0.919**  
  KEAD 의무고용율 교차 2특성 결합 → Ablation +20.4%p

---

## 연동 데이터 (8기관)

| 기관 | 데이터 |
|---|---|
| 한국장애인고용공단 | 근로지원인 구인·수행기관·고용개발원 보고서 |
| 한국고용정보원 | 워크넷 직업·직무(NCS)·훈련·취업역량 API |
| 고용노동부 | 체불사업주 명단 (789건 실데이터) |
| 국민연금공단 | 사업장 가입자 시계열 Z-score |
| 금융감독원 | DART 재무제표 (부채비율·영업손실·자본잠식) |
| 건강보험공단 | 직장가입자 현황 (4대보험 삼각검증) |
| 근로복지공단 | 고용·산재보험 |
| 국세청 | 사업자 상태 조회 |

---

## 디렉토리

```
├── run.bat / run.ps1 / run.sh
├── requirements.txt
├── app/
│   ├── main.py / settings.py / db.py / ingest.py
│   ├── routes/          API 라우터 (40+ 모듈)
│   ├── templates/       화면 (40+ 페이지)
│   └── static/
│       ├── wageguard-sdk.js   부정수급 차단 SDK
│       ├── korea_map.js       17 광역시도 타일맵
│       └── format.js
├── scripts/             데이터 수집·시드 스크립트
├── samples/             공공데이터 응답 샘플·시뮬 결과
└── proposal/            기획 문서
```

---

## 환경변수 (.env)

```
DATA_GO_KR_KEY=        # data.go.kr 인증키
OPENDART_KEY=          # DART 전자공시
NAVER_CLIENT_ID=       # 네이버 검색 API
NAVER_CLIENT_SECRET=
GOOGLE_PLACES_API_KEY= # Google Places
WORK24_AUTH_KEY_JOB=   # 고용정보원 직업정보
WORK24_AUTH_KEY_DUTY=
WORK24_AUTH_KEY_TRAINING=
WORK24_AUTH_KEY_CAREER=
```

키 없이도 로컬 샘플 데이터로 전체 기능 작동합니다.

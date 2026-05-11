# WageGuard — 장애인 노동 사각지대 점검 우선순위 AI

> 제5회 고용노동 공공데이터·AI 활용 공모전 출품작 (제품·서비스 개발 부문)

---

## 한 줄 정의

매칭은 워크넷이, 적발은 운영주체가, 우선순위는 **WageGuard**가 정렬합니다. 8기관 공개 데이터 교차 의심도 산출 + 명의도용 부정수급 실시간 차단 SDK.

---

## 즉시 가동

```
run.bat       (Windows)
run.ps1       (PowerShell)
run.sh        (Git Bash / WSL)
```

브라우저: **http://127.0.0.1:8123**

---

## 핵심 화면 (사용자별)

| 사용자 | 진입 |
|---|---|
| 🛡 Track A | `/m6` SDK 가동 / `/m6/embed-demo` 한 줄 이식 데모 |
| 📊 Track B | `/triage` 점검 우선순위 (메인) / `/verify` 7-step 라이브 검증 |
| 🧑‍💼 운영주체 | `/operator` 점검 시뮬레이션 / `/judge` 평가위원 가이드 |
| 🤖 AI/ML | `/ml` 정식 학습 모델 / `/disability` 주관기관 데이터 결합 |
| 👤 근로자 | `/me` 내 사업장 점수 / `/cases` 신고하기 / `/wage-calc` 체불액 / `/kakao` 카톡 봇 |
| 📊 통계 | `/` 전국 / `/industry` 업종 / `/region` 지역 / `/graph` 대표자 추적 |
| ℹ︎ 자료 | `/onepager` 요약 / `/evidence` 효과 근거 / `/health` 데이터 출처 |

---

## 차별화 한 줄

**현장 제보 우회 사례 기반 SDK** — 익명 제보로 확인된 해외 체류 중 한국 본가 PC 원격 접속 부정수급 우회 패턴. 그 우회를 잡는 9개 신호 한 줄 이식 → 장애인 고용장려금·실업급여 동시 보호.

---

## 검증된 데이터 (8기관 교차)

- **한국장애인고용공단** (근로지원인 구인·수행기관·보고서)
- **한국고용정보원** (워크넷 직업·직무·훈련·경보 API)
- 국세청 사업자상태 (15081808)
- 근로복지공단 고용/산재 (15059256)
- 국민연금공단 (가입자 현황 시계열)
- 금융감독원 DART (재무제표 선행지표)
- 건강보험공단 (4대보험 삼각검증)
- 고용노동부 (체불사업주 명단)

---

## 디렉토리

```
c:\lsc\Moel\
├── run.bat / run.ps1 / run.sh / stop.bat
├── requirements.txt / .env / .env.example
├── app/                          시제품
│   ├── main.py / settings.py / db.py / scheduler.py
│   ├── routes/                   40+ API 모듈
│   ├── templates/                40+ 화면
│   └── static/
│       ├── format.js
│       ├── korea_map.js          한국 17 광역시도 타일맵
│       └── WageGuard-sdk.js     실시간 차단 SDK (한 줄 이식)
├── scripts/                      검증 스크립트
├── samples/                      공공데이터 응답·EDA·시뮬 결과
├── proposal/
│   ├── business_plan.md          사업계획서 (핵심)
│   ├── data_spec_form.md         데이터 명세서 (핵심)
│   ├── video_script.md           시연 스크립트 (핵심)
│   └── TRIZ_analysis.md          TRIZ 발명원리 적용
└── data/WageGuard.sqlite         자동 생성
```


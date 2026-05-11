# 시제품 공개 호스팅 가이드 — 발표심사 자격 확보

> 공모전 공고문: 제품·서비스 개발 부문은 **발표심사 전까지 시제품 구체화 필수**.
> 인정 기준: 앱스토어 등록 / 특허출원 / **서비스 개시** 중 1개 이상.
> 마감: 발표심사일(2026.7.21) 이전, 가능하면 1차 발표(6.17) 전.

---

## 옵션 비교

| 옵션 | 난이도 | 비용 | 소요 시간 | 추천 |
|---|---|---|---|---|
| **A. 무료 PaaS 서비스 개시** | 쉬움 | 무료 | 1~2시간 | ⭐⭐⭐ 1순위 |
| B. 앱스토어 등록 | 중간 | 12만원/년 | 1~2주 (심사) | △ |
| C. 특허출원 | 어려움 | 100만원+ | 4~6주 | × 시간 부족 |
| D. 도메인 + VPS | 중간 | 1.5만원/월 + 도메인 | 1일 | ⭐⭐ 2순위 |

---

## 옵션 A — Render.com 무료 호스팅 (1~2시간) **권장**

### 준비물
- GitHub 계정 (이미 있을 듯)
- Render.com 계정 (GitHub 로그인)
- 본 WageGuard 코드 GitHub repo로 push

### 절차

**1. requirements.txt 확인** (이미 존재)
```
fastapi uvicorn jinja2 python-dotenv httpx beautifulsoup4 lxml reportlab python-multipart
```

**2. `render.yaml` 생성** (저장소 루트)
```yaml
services:
  - type: web
    name: WageGuard
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    plan: free
    envVars:
      - key: DATA_GO_KR_KEY
        sync: false
      - key: NAVER_CLIENT_ID
        sync: false
      - key: NAVER_CLIENT_SECRET
        sync: false
      - key: GOOGLE_PLACES_API_KEY
        sync: false
      - key: OPENDART_KEY
        sync: false
      - key: WORK24_AUTH_KEY_JOB
        sync: false
      - key: WORK24_AUTH_KEY_DUTY
        sync: false
      - key: WORK24_AUTH_KEY_TRAINING
        sync: false
      - key: WORK24_AUTH_KEY_CAREER
        sync: false
```

**3. GitHub 푸시**
```
git init
git add .
git commit -m "WageGuard v1.0 - 공모전 출품"
gh repo create WageGuard --public --source=. --push
```

**4. Render 연결**
1. https://dashboard.render.com/ 접속 → New + → Web Service
2. GitHub repo 선택 → payradar
3. 환경변수 4개 (위 envVars) 직접 입력 (`.env.example` 값 그대로)
4. Create Web Service 클릭

**5. 도메인 확인**
- 자동 발급: `https://WageGuard-xxxx.onrender.com`
- 발표 자료에 이 URL 기재 → "서비스 개시" 요건 충족

### 주의사항
- 무료 플랜은 15분 무활동 시 슬립 → 첫 요청 시 30초 지연
- 발표 직전 한 번 ping 보내서 깨우기
- `data/payradar.sqlite` SQLite는 ephemeral storage — 재기동 시 ingest 다시 (앱이 자동 처리하므로 OK)

---

## 옵션 D — 도메인 + Caddy + VPS (도메인이 더 인상적)

### 준비물
- 도메인 (가비아 또는 Cloudflare Registrar — `payradar.kr` 1.2만원/년)
- VPS (Vultr/DigitalOcean/AWS Lightsail $5/월)

### 절차

**1. 도메인 등록**
- https://www.cloudflare.com/products/registrar/ — `.com` $9/년
- 또는 가비아 `.kr` 1.5만원/년

**2. VPS 생성 (Ubuntu 22.04, 1GB RAM)**

**3. 서버 셋업** (SSH로 VPS 접속 후)
```bash
sudo apt update && sudo apt install -y python3-venv git caddy
cd /opt && sudo git clone https://github.com/<your-id>/payradar.git
cd payradar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env 파일 직접 편집 (인증키 입력)
sudo nano /etc/systemd/system/payradar.service
```

**4. systemd 서비스**
```ini
[Unit]
Description=WageGuard
After=network.target

[Service]
WorkingDirectory=/opt/payradar
ExecStart=/opt/payradar/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8123
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now payradar
```

**5. Caddyfile** (`/etc/caddy/Caddyfile`)
```
payradar.kr {
    reverse_proxy 127.0.0.1:8123
}
```

```bash
sudo systemctl reload caddy
```

**6. DNS A 레코드**
- `payradar.kr A <VPS IP>`

→ HTTPS 자동 발급 (Caddy + Let's Encrypt) — `https://payradar.kr` 즉시 접속 가능.

---

## 발표 자료에 기재할 문구 예시

> **시제품 운영 상태 (2026.X.X 기준)**
> - 라이브 URL: https://WageGuard-xxxx.onrender.com (또는 https://WageGuard.kr)
> - 가동 중인 페이지: 47 / API: 153 (총 200 라우트)
> - 핵심 모듈 즉시 시연: `/triage`, `/verify`, `/dart`, `/pension`, `/insurance-cross`, `/m6/embed-demo`, `/ml`, `/disability`
> - 동영상 시연: 별도 첨부 파일 (`WageGuard_시연_이성철_2026.mp4`)

---

## 권장 일정 (2026년 5월 기준)

| 날짜 | 작업 |
|---|---|
| **5/8 (목)** | GitHub repo + render.yaml 푸시 → Render 배포 |
| **5/9 (금)** | 환경변수 입력 + 첫 ping 확인 + 도메인 (옵션) |
| **5/10~13** | 발표 자료에 라이브 URL 반영 + 동영상 녹화 |
| **5/14 16:00** | 출품 마감 — 사업계획서 + 데이터명세서 + 라이브 URL 첨부 |
| **6/17** | 1차 통과 발표 |
| **7/6~10** | 멘토링 (1차 통과 시) |
| **7/21** | 발표심사 — 라이브 URL + 동영상 시연 |

---

## 출품자 직접 액션 항목

- [ ] GitHub 계정 확인 / 신규 repo 생성
- [ ] Render.com 계정 (GitHub 로그인) — 5분
- [ ] `render.yaml` 추가 + push
- [ ] 환경변수 4개 입력
- [ ] 도메인 등록 (선택)
- [ ] 최초 배포 후 5개 이상 페이지 즉시 접속 검증
- [ ] 발표 자료에 라이브 URL 반영

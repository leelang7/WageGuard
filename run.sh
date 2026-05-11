#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PORT=8123
HOST=127.0.0.1

echo "============================================================"
echo " WageGuard  -  http://$HOST:$PORT"
echo "============================================================"

# 1) 의존성
python -c "import fastapi, uvicorn, requests, dotenv, bs4, lxml, jinja2, multipart" 2>/dev/null || {
    echo "[setup] 의존성 설치..."
    python -m pip install -q -r requirements.txt
}

# 2) .env
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "[setup] .env 없음 - .env.example 복사"
    else
        echo "[!] .env / .env.example 둘 다 없음."
    fi
fi

# 3) 포트 점유 종료 (Windows Git Bash / Linux 모두)
if command -v netstat >/dev/null 2>&1; then
    PID=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep LISTENING | awk '{print $5}' | head -1)
    if [ -n "$PID" ]; then
        echo "[setup] 기존 프로세스 PID $PID 종료"
        taskkill //F //PID "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null || true
    fi
fi

# 4) DB 적재
echo "[data] SQLite 초기화 + 체불사업주/위험셀 적재"
python -m app.ingest

# 5) NPS CSV 선택 적재
if ls samples/국민연금*.csv >/dev/null 2>&1; then
    echo "[data] 국민연금 CSV 발견 - 적재"
    python -m scripts.ingest_nps
fi

# 6) 서버
echo
echo "[run] uvicorn 시작 - 종료하려면 Ctrl+C"
echo
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"

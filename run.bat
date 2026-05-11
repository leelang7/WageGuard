@echo off
setlocal
cd /d %~dp0

set PORT=8123
set HOST=127.0.0.1
set PYTHONHASHSEED=0

echo ============================================================
echo  WageGuard  -  http://%HOST%:%PORT%
echo ============================================================

REM 1) deps
python -c "import fastapi, uvicorn, requests, dotenv, bs4, lxml, jinja2, multipart" 1>nul 2>nul
if errorlevel 1 (
    echo [setup] installing dependencies...
    python -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo [error] pip install failed
        exit /b 1
    )
)

REM 2) .env
if not exist .env (
    if exist .env.example (
        echo [setup] copy .env.example -^> .env
        copy /Y .env.example .env 1>nul
    ) else (
        echo [warn] no .env / .env.example
    )
)

REM 3) kill any process holding the port
for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    echo [setup] killing existing PID %%A
    taskkill /F /PID %%A 1>nul 2>nul
)

REM 4) ingest base data
echo [data] ingest defaulters + risk_cells
python -m app.ingest
if errorlevel 1 (
    echo [error] ingest failed
    exit /b 1
)

REM 5) 체불사업주 합성 확장 (실 789건 + 합성 → 3000건)
echo [data] seeding extended defaulters (real 789 + synthetic)
python scripts\seed_defaulters_extended.py 3000

REM 6) NPS ingest: 실제 CSV 있으면 전체 적재, 없으면 20000건 시드
dir /b samples\*.csv 2>nul | findstr /i "nps national_pension 국민연금" 1>nul 2>nul
if not errorlevel 1 (
    echo [data] NPS CSV detected - ingesting full dataset
    python -m scripts.ingest_nps
) else (
    echo [data] NPS CSV not found - seeding 20000 synthetic workplaces
    python scripts\seed_nps_extended.py 20000
)

REM 7) start server
echo.
echo [run] starting uvicorn (Ctrl+C to stop)
echo.
python -m uvicorn app.main:app --host %HOST% --port %PORT%

endlocal

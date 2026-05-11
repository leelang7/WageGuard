$ErrorActionPreference = 'Stop'
$Port = 8123
$AppHost = '127.0.0.1'

Set-Location -Path $PSScriptRoot

Write-Host "============================================================"
Write-Host " WageGuard  -  http://$AppHost`:$Port"
Write-Host "============================================================"

# 1) 의존성
$check = & python -c "import fastapi, uvicorn, requests, dotenv, bs4, lxml, jinja2, multipart" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[setup] 의존성 설치..."
    & python -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install 실패" }
}

# 2) .env
if (-not (Test-Path .env)) {
    if (Test-Path .env.example) {
        Copy-Item .env.example .env -Force
        Write-Host "[setup] .env 없음 - .env.example 복사"
    } else {
        Write-Warning ".env / .env.example 둘 다 없음."
    }
}

# 3) 포트 점유 종료
$pids = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
foreach ($p in $pids) {
    Write-Host "[setup] 기존 프로세스 PID $p 종료"
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
}

# 4) DB 적재
Write-Host "[data] SQLite 초기화 + 체불사업주/위험셀 적재"
& python -m app.ingest
if ($LASTEXITCODE -ne 0) { throw "ingest 실패" }

# 5) NPS CSV 선택 적재
$nps = Get-ChildItem samples -Filter "국민연금*.csv" -ErrorAction SilentlyContinue
if ($nps) {
    Write-Host "[data] 국민연금 CSV 발견 - 적재"
    & python -m scripts.ingest_nps
}

# 6) 서버
Write-Host ""
Write-Host "[run] uvicorn 시작 - 종료하려면 Ctrl+C"
Write-Host ""
& python -m uvicorn app.main:app --host $AppHost --port $Port

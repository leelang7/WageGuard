@echo off
set PORT=8123
for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    echo killed PID %%A
    taskkill /F /PID %%A 1>nul 2>nul
)
echo done.

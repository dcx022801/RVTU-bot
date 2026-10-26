@echo off
setlocal
cd /d "%~dp0.."

set "CONFIG_DIR=%CD%\state\config"
set "DEFAULTS_DIR=%CD%\defaults"
set "STATE_DIR=%CD%\state"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
    echo 用法：win\run.bat app\scripts\checkin.py --dry-run
    echo 可选脚本：checkin.py / happy_checkin.py / portal_auth.py / refresh_creds.py / notify.py
    exit /b 1
)

"%PY%" %*

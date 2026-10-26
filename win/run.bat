@echo off
setlocal
cd /d "%~dp0.."

if "%~1"=="" goto usage
if "%~2"=="" goto usage

set "PROFILE=%~1"
if not exist "state\users\%PROFILE%\config" (
    echo [错误] 用户不存在：state\users\%PROFILE%\config
    exit /b 1
)

set "CONFIG_DIR=%CD%\state\users\%PROFILE%\config"
set "DEFAULTS_DIR=%CD%\defaults"
set "STATE_DIR=%CD%\state\users\%PROFILE%\state"
set "RVTU_PROFILE=%PROFILE%"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~2" %3 %4 %5 %6
exit /b %errorlevel%

:usage
echo 用法：win\run.bat ^<用户标识^> app\scripts\checkin.py --dry-run
echo 示例：win\run.bat a app\scripts\happy_checkin.py --precheck --dry-run
echo 可选脚本：checkin.py / happy_checkin.py / portal_auth.py / refresh_creds.py / notify.py
exit /b 1

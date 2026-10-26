@echo off
setlocal
cd /d "%~dp0.."

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.py" %*
    exit /b 0
)

where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 "%~dp0gui.py" %*
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0gui.py" %*
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    start "" py -3 "%~dp0gui.py" %*
    exit /b 0
)

echo [错误] 未找到 Python。请安装 Python 3.10+ 后运行 win\install.bat。
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "PY=python"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python 3，请安装 Python 3.10+ 并勾选 Add Python to PATH。
    pause
    exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/3] 创建独立虚拟环境 win\.venv ...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 已存在虚拟环境 win\.venv
)

echo [2/3] 安装依赖 pystray / Pillow ...
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或 pip 配置。
    pause
    exit /b 1
)

echo [3/3] 安装完成。双击 win\start.bat 启动 RVTU-bot。
echo 提示：移动项目目录后需删除 win\.venv 并重新运行本脚本。
pause

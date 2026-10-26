@echo off
setlocal
cd /d "%~dp0.."

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [错误] 未找到 win\.venv，请先运行 win\install.bat。
    pause
    exit /b 1
)

set "PY=%~dp0.venv\Scripts\python.exe"
echo [1/2] 安装构建依赖 PyInstaller ...
"%PY%" -m pip install -r "%~dp0requirements-build.txt"
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，请检查网络或 pip 配置。
    pause
    exit /b 1
)

echo [2/2] 构建 Windows 便携包 ...
"%PY%" "%~dp0build_exe.py" %*
if errorlevel 1 (
    echo [错误] 构建失败。
    pause
    exit /b 1
)

echo 构建完成，见 dist\ 目录。
pause

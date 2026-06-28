@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal EnableDelayedExpansion

set /a PASS=0
set /a WARN=0
set /a FAIL=0

call :title "AI 审稿系统 - 一键环境自检"

call :check_file "requirements.txt" "依赖清单 requirements.txt"
call :check_file "run_desktop.py" "桌面入口 run_desktop.py"

set "PYTHON_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    call :ok "已检测到虚拟环境 Python: .venv\\Scripts\\python.exe"
) else (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
    if not defined PYTHON_CMD (
        python --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
    if defined PYTHON_CMD (
        call :warn "未检测到 .venv，当前可用 Python: !PYTHON_CMD!"
    ) else (
        call :fail "未检测到可用 Python（py -3 / python 均不可用）"
    )
)

if defined PYTHON_CMD (
    for /f "delims=" %%V in ('cmd /c "!PYTHON_CMD! --version 2^>^&1"') do set "PY_VER=%%V"
    if defined PY_VER call :ok "Python 版本: !PY_VER!"
)

if exist ".venv\Scripts\python.exe" (
    call .venv\Scripts\python.exe -c "import fastapi,uvicorn,docx" >nul 2>nul
    if errorlevel 1 (
        call :warn "虚拟环境依赖不完整（fastapi/uvicorn/python-docx 可能缺失）"
    ) else (
        call :ok "核心依赖检查通过（fastapi/uvicorn/python-docx）"
    )
)

if exist "dist\AI审稿工具.exe" (
    call :ok "检测到 EXE: dist\\AI审稿工具.exe"
) else (
    call :warn "未检测到 EXE（可运行 build_exe.bat 生成）"
)

call :check_port 8010 "默认服务端口"
call :check_port 8011 "候选服务端口"
call :check_port 11434 "本地 Ollama 常用端口"

echo.
echo ============================================
echo 自检完成：通过 !PASS!  项，警告 !WARN!  项，失败 !FAIL!  项
echo ============================================

if !FAIL! GTR 0 (
    echo [建议] 先处理失败项，再执行 启动工具.bat
) else (
    if !WARN! GTR 0 (
        echo [建议] 可继续使用，但建议按提示处理警告项以获得最佳体验
    ) else (
        echo [建议] 环境状态良好，可直接执行 启动工具.bat
    )
)

echo.
pause
exit /b 0

:check_file
set "CHK_PATH=%~1"
set "CHK_DESC=%~2"
if exist "%CHK_PATH%" (
    call :ok "%CHK_DESC% 存在"
) else (
    call :fail "%CHK_DESC% 缺失"
)
exit /b 0

:check_port
set "P=%~1"
set "PDESC=%~2"
netstat -ano | findstr /R /C:":%P% .*LISTENING" >nul
if not errorlevel 1 (
    call :warn "%PDESC% %P% 已被占用"
) else (
    call :ok "%PDESC% %P% 可用"
)
exit /b 0

:title
echo ============================================
echo %~1
echo ============================================
exit /b 0

:ok
set /a PASS+=1
echo [通过] %~1
exit /b 0

:warn
set /a WARN+=1
echo [警告] %~1
exit /b 0

:fail
set /a FAIL+=1
echo [失败] %~1
exit /b 0

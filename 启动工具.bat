@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   AI 专业编辑审稿系统  ^|  六维并行分析引擎
echo ============================================
echo.
if exist ".\dist\AI审稿工具.exe" (
	echo 检测到 EXE，正在启动（无需 Python）...
	start "" ".\dist\AI审稿工具.exe"
	goto :eof
)

echo 未检测到 dist\AI审稿工具.exe，回退到源码模式启动...
echo 本机访问:    http://localhost:8010 (默认起始端口)
echo 局域网/热点: 启动后终端会显示局域网地址
echo.

if not exist ".venv\Scripts\python.exe" (
	echo [错误] 未找到 .venv\Scripts\python.exe
	echo 请先运行 安装依赖.bat 安装依赖，
	echo 或先打包生成 dist\AI审稿工具.exe 后再运行本脚本。
	echo.
	pause
	exit /b 1
)

call .venv\Scripts\python.exe run_desktop.py

pause

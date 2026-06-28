@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   AI 专业编辑审稿系统  ^|  六维并行分析引擎
echo ============================================
echo.
echo 正在启动服务，稍后将自动打开浏览器...
echo 如果是首次运行，请先双击 installdepdence.bat 安装依赖。
echo.
echo 本机访问:    http://localhost:8010 (默认起始端口)
echo 局域网/热点: 启动后终端会显示局域网地址
echo.

.venv\Scripts\python run_desktop.py

pause

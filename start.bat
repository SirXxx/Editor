@echo off
chcp 65001 >nul
cd /d E:\Document\Learn\Editor

echo 正在启动 AI 审稿工具...
echo 如果是首次运行，请先双击"installdepdence.bat"
echo.
.venv\Scripts\python run_desktop.py

pause

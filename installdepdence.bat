@echo off
chcp 65001 >nul
cd /d E:\Document\Learn\Editor

echo 正在安装/更新依赖到虚拟环境...
echo.
.venv\Scripts\pip install -r requirements.txt

pause

@echo off
cd /d E:\Document\Learn\Editor

echo [1/3] 安装 PyInstaller...
.venv\Scripts\pip install pyinstaller

echo [2/3] 开始打包 EXE...
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --name AI审稿工具 run_desktop.py

echo [3/3] 打包完成。
echo EXE 输出目录：E:\Document\Learn\Editor\dist
pause

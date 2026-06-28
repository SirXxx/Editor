@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="
set "REBUILD_VENV=0"

if exist ".venv\Scripts\python.exe" (
	call .venv\Scripts\python.exe --version >nul 2>nul
	if errorlevel 1 (
		echo [警告] 检测到 .venv，但当前虚拟环境不可用（可能来自其他电脑拷贝）。
		set "REBUILD_VENV=1"
	) else (
		set "PYTHON_CMD=.venv\Scripts\python.exe"
	)
)

if not defined PYTHON_CMD (
	py -3 --version >nul 2>nul
	if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
	python --version >nul 2>nul
	if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
	echo [错误] 未找到可用 Python。请先安装 Python 3 并勾选 "Add Python to PATH"。
	echo.
	pause
	exit /b 1
)

if "%REBUILD_VENV%"=="1" (
	echo [信息] 正在重建虚拟环境 .venv ...
	rmdir /s /q .venv >nul 2>nul
)

if not exist ".venv\Scripts\python.exe" (
	echo [1/4] 首次运行，正在创建虚拟环境 .venv ...
	call %PYTHON_CMD% -m venv .venv
	if errorlevel 1 (
		echo [错误] 创建虚拟环境失败，请检查 Python 安装是否完整。
		echo.
		pause
		exit /b 1
	)
)

echo [2/4] 正在升级 pip ...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
	echo [错误] pip 升级失败。
	echo.
	pause
	exit /b 1
)

echo [3/4] 正在安装 PyInstaller ...
call .venv\Scripts\python.exe -m pip install pyinstaller
if errorlevel 1 (
	echo [错误] 安装 PyInstaller 失败，请检查网络或镜像源。
	echo.
	pause
	exit /b 1
)

echo [4/4] 正在打包 EXE ...
call .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name AI审稿工具 run_desktop.py
if errorlevel 1 (
	echo [错误] EXE 打包失败。
	echo.
	pause
	exit /b 1
)

echo.
echo 打包完成。
echo EXE 输出目录：%CD%\dist
pause

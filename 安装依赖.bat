@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="

if exist ".venv\Scripts\python.exe" (
	set "PYTHON_CMD=.venv\Scripts\python.exe"
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

if not exist ".venv\Scripts\python.exe" (
	echo [1/3] 首次运行，正在创建虚拟环境 .venv ...
	call %PYTHON_CMD% -m venv .venv
	if errorlevel 1 (
		echo [错误] 创建虚拟环境失败，请检查 Python 安装是否完整。
		echo.
		pause
		exit /b 1
	)
)

echo [2/3] 正在升级 pip ...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
	echo [错误] pip 升级失败。
	echo.
	pause
	exit /b 1
)

echo [3/3] 正在安装/更新依赖 ...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
	echo [错误] 依赖安装失败，请检查网络或镜像源配置。
	echo.
	pause
	exit /b 1
)

echo.
echo 依赖安装完成。

pause

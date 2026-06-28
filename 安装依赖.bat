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

echo [3/5] 正在安装核心依赖 ...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
	echo [错误] 核心依赖安装失败，请检查网络或镜像源配置。
	echo.
	pause
	exit /b 1
)

echo [4/5] 正在安装 OCR 可选依赖（paddle）...
call .venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
if errorlevel 1 (
	echo [警告] OCR 可选依赖安装失败（通常是 Python 版本与 paddlepaddle 轮子不兼容）。
	echo [警告] 主程序仍可使用；若需 OCR，建议使用 Python 3.10/3.11 后重试。
) else (
	echo OCR 可选依赖安装完成。
)

echo [5/5] 正在安装表格提取可选依赖（camelot）...
call .venv\Scripts\python.exe -m pip install -r requirements-table.txt
if errorlevel 1 (
	echo [警告] 表格提取可选依赖安装失败（camelot 可能受系统环境影响）。
	echo [警告] 主程序仍可使用；仅高级表格提取能力受影响。
) else (
	echo 表格提取可选依赖安装完成。
)

echo.
echo 依赖安装完成（核心必装 + OCR/表格提取可选）。

pause

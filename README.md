# AI 审稿系统（PDF + Word + Word报告导出）

当前版本已支持：
- PDF 输入
- Word（.docx）输入
- 拖拽上传
- 真实后端任务队列
- 任务状态轮询
- 历史任务列表
- Word（.docx）审稿报告导出
- 桌面启动入口与 EXE 打包脚本

## 一、开发方式启动
```bash
# 先进入项目根目录（本仓库）
python -m pip install -r requirements.txt
python -m uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8010
```
浏览器打开：
```text
http://localhost:8010/
```

## 二、桌面方式启动
直接运行：
```text
启动工具.bat
```
或：
```bash
python run_desktop.py
```
说明：
- `启动工具.bat` 会优先启动 `dist\AI审稿工具.exe`（无需 Python）。
- 若未检测到 EXE，则自动回退到源码模式（需要 `.venv` 和依赖）。
- 源码模式会自动寻找可用端口并打开浏览器。

## 三、打包成 EXE
直接运行：
```text
build_exe.bat
```
或手动执行：
```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --name AI审稿工具 run_desktop.py
```
打包完成后，EXE 位于：
```text
dist\AI审稿工具.exe
```

## 四、说明
当前仍是“本地服务 + 本地界面”的桌面化封装方案，适合先快速落地使用；后续如需要可继续升级为 Electron / Tauri 真正桌面壳。

OCR 依赖说明：
- `paddleocr`/`paddlepaddle` 为可选依赖，安装脚本会在核心依赖后自动尝试安装。
- 若提示 `paddlepaddle` 无可用版本，通常是当前 Python 版本不受支持（Windows 常见于 3.12+）。
- 不影响主程序启动；仅 OCR 功能会不可用。
- 如需 OCR，建议使用 Python 3.10 或 3.11 后重新执行 `安装依赖.bat`。

表格提取依赖说明：
- `camelot-py` 为可选依赖，安装脚本会自动尝试安装。
- 若安装失败，不影响主程序启动；仅高级表格提取能力会降级。

换机提示：
- 如果项目目录是从旧电脑直接拷贝过来，脚本会自动识别并重建不可用的 `.venv`，无需手动删除。


## 五、推荐的 BAT 打开方式
首次使用：
```text
安装依赖.bat
```
日常启动：
```text
启动工具.bat
```
环境诊断：
```text
环境自检.bat
```
说明：脚本已精简，常用入口为：`安装依赖.bat`、`启动工具.bat`、`环境自检.bat`、`build_exe.bat`。

## 六、一键自检说明
`环境自检.bat` 会检查：
- 关键文件是否存在（`requirements.txt`、`run_desktop.py`）
- Python 是否可用、`.venv` 是否存在
- 核心依赖是否可导入（fastapi / uvicorn / python-docx）
- 是否已生成 EXE（`dist\\AI审稿工具.exe`）
- 常用端口是否占用（8010、8011、11434）

输出结果会按“通过 / 警告 / 失败”汇总，并给出下一步建议。

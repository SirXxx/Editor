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
cd /d E:\Document\Learn\Editor
python -m pip install -r requirements.txt
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8010
```
浏览器打开：
```text
http://127.0.0.1:8010/
```

## 二、桌面方式启动
直接运行：
```text
start_desktop.bat
```
或：
```bash
python run_desktop.py
```
说明：会自动寻找可用端口，并自动打开浏览器。

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
E:\Document\Learn\Editor\dist\AI审稿工具.exe
```

## 四、说明
当前仍是“本地服务 + 本地界面”的桌面化封装方案，适合先快速落地使用；后续如需要可继续升级为 Electron / Tauri 真正桌面壳。


## 五、推荐的 BAT 打开方式
首次使用：
```text
安装依赖.bat
```
日常启动：
```text
启动工具.bat
```
说明：`启动工具.bat` 会调用 `run_desktop.py`，自动寻找可用端口并自动打开浏览器。

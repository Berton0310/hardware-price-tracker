---
trigger: glob
globs: "*.bat, *.ps1, scripts/**, run_pipeline.py, login_fb.py"
---

# Windows 腳本與 CLI 規則

- `.bat` 檔必須是 CRLF 換行；`echo` 的文字不要含未跳脫的 `(`、`)`、`%`、`^`（ERR-017）。
- 含中文的 `.bat`／`.ps1` 要注意 ANSI 與 UTF-8 BOM 的相容性；批次檔的分支用 `goto`，不用多行括號區塊（ERR-016）。
- 批次檔不要直接呼叫 `python`，要偵測 Conda／venv 並確認套件可用（ERR-018）。
- CLI 腳本要 `print` 非 cp950 字元前，先 `sys.stdout.reconfigure(encoding="utf-8")`（ERR-012）。

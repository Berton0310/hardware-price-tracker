# 電腦硬體新品歷史價格與二手市場行情比價分析系統

期中專題。抓取原價屋的新品報價，以及 PTT HardwareSale、Facebook 二手社團的刊登，存進 SQLite，計算歷史價格位階、二手折數、真假降價與異常低價，並用 Streamlit 儀表板呈現。

## 專案結構

```text
midterm_project/
├── crawlers/          # 原價屋 (requests + bs4)、PTT (requests + bs4)、FB 社團 (Playwright) 爬蟲
├── cleaners/          # 二手貼文清洗：OpenRouter LLM 解析與規則式 fallback
├── database/          # SQLite（sqlite3）連線、建表與 CRUD
├── analysis/          # 價格位階、二手折數、真假降價、異常低價
├── services/          # 爬蟲 → 清洗 → 入庫的同步流程
├── app/               # Streamlit 互動式比價儀表板
├── scripts/           # 每日排程與手動爬取腳本
├── tests/             # pytest（離線測試，fixtures 放 HTML 快照）
├── docs/              # 進度、協作流程與各模組規格
├── run_pipeline.py    # CLI 入口
├── start_project.bat  # 一鍵啟動儀表板
└── daily_crawl.bat    # 每日自動同步（Windows 工作排程器）
```

## 安裝

需要 Python 3.12 以上。

```powershell
pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # 填入 OPENROUTER_API_KEY
```

FB 社團需要登入，先執行 `python login_fb.py` 存下登入 cookie。cookie 只存在本機，已被 `.gitignore` 排除。

## 使用

```powershell
python run_pipeline.py --sync-coolpc    # 同步原價屋新品報價
python run_pipeline.py --sync-ptt       # 同步 PTT 二手刊登
python run_pipeline.py --sync-daily     # 每日全量同步（原價屋 + PTT + FB）
python run_pipeline.py --analysis       # 在終端機輸出比價分析摘要
python run_pipeline.py --app            # 啟動 Streamlit 儀表板
python run_pipeline.py --help           # 所有參數（含 --db 指定資料庫路徑）
```

也可以直接雙擊 `start_project.bat` 開啟儀表板。

## 測試

```powershell
python -m pytest tests/ -v
ruff check .
```

測試以離線方式執行（網頁用 fixtures 快照、網路與 LLM 用 mock），不會讀寫 `data/` 的正式資料庫。

## 開發規範

給人與 AI Agent 共用的開發規範在 [AGENTS.md](AGENTS.md)，模組規則在 `.agents/rules/`，協作流程在 [docs/workflow.md](docs/workflow.md)。

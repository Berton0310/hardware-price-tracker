# AGENTS.md

電腦硬體新品歷史價格與二手市場行情比價分析系統：抓取原價屋新品價、PTT HardwareSale 與 FB 二手社團行情，存進 SQLite，計算歷史價格位階、二手折數與異常低價，用 Streamlit 呈現。

本檔是所有 Agent 共用的規範（Antigravity 每輪自動載入；Claude Code 透過 `CLAUDE.md` 引用）。各模組的細部規則與歷史教訓放在 `.agents/rules/`，修改對應目錄時會自動載入。

## 目錄與職責

| 路徑 | 職責 |
| :--- | :--- |
| `crawlers/` | 抓取與解析原始頁面，回傳 dict；不碰資料庫 |
| `cleaners/` | 二手貼文清洗（OpenRouter LLM 與規則式 fallback） |
| `database/` | SQLite（`sqlite3`）連線、建表與 CRUD |
| `analysis/` | 純運算：位階、折數、真假降價、異常低價；不碰 I/O 與 UI |
| `services/` | 爬蟲 → 清洗 → 入庫的同步流程 |
| `app/` | Streamlit UI；只呼叫 `database/`、`analysis/` 的函式，不寫 SQL、不呼叫爬蟲 |
| `scripts/`、`*.bat` | 每日排程與手動爬取腳本 |
| `run_pipeline.py` | CLI 入口 |
| `tests/` | pytest；`tests/fixtures/` 放離線 HTML 快照與 mock 資料 |
| `docs/` | 規格與進度，入口是 `docs/index.md` |
| `data/` | SQLite 資料庫檔（已被 `.gitignore` 排除） |

## 指令（PowerShell，Python 3.12）

這台機器有多個 Python（ERR-018），先啟用專案環境，再一律用 `python -m` 呼叫工具。

```powershell
pip install -r requirements.txt
playwright install chromium
python -m pytest tests/ -v
python -m pytest tests/test_crawlers.py -v
ruff check .
streamlit run app/main_app.py
python run_pipeline.py --help
```

## 寫程式的原則

- 只做當前需求要的功能。不預留抽象層、未使用的參數或用不到的防禦程式碼。
- 模組結構已經混亂時，重寫該模組比疊加補丁好，但要保持對外函式簽名不變、既有測試仍然通過。需要改簽名時先問使用者。
- 修改過的檔案要讓 `ruff check <檔案>` 沒有錯誤（未使用的 import／變數、`print`）。
- 模組對外公開的函式要標註參數與回傳型別。
- 網路請求一定設 `timeout`，只捕捉具體例外（如 `requests.exceptions.RequestException`、Playwright 的 `TimeoutError`），不寫 `except: pass`。
- 單筆資料解析失敗時，回傳 `None` 或跳過該筆，並用 `logging` 記下欄位與原始值。不要用 `0` 當預設價格：`0` 會被算進中位數與百分位，扭曲分析結果（ERR-002、ERR-015）。
- 函式庫模組用 `logging`。只有 CLI 入口（`run_pipeline.py`、`login_fb.py`、`scripts/`）可以 `print`。
- 原價屋頁面用 `cp950` 解碼（cp950 是 Big5 的超集，用 `big5` 會遇到缺字）；其他檔案與模組一律 `utf-8`。

## 爬蟲與機密資料

- 測試不發真實網路請求：網頁用 `tests/fixtures/` 快照，HTTP 與 LLM API 用 mock。
- 請求之間保持現有間隔（PTT 1.2–2.0 秒、FB 2.5–4.5 秒），不要調低。
- FB 使用條款禁止未經許可的自動化擷取，FB 爬蟲只做課堂研究用途的低頻抓取。
- 帳密、API key、cookie 只放 `.env` 與 `.gitignore` 已排除的 cookie 檔，不寫進程式碼、測試、fixtures 或文件。

## 文件與錯誤紀錄

- 開工前讀 `docs/progress.md` 和任務對應的 `docs/modules/0X_*.md`，用不到的模組文件不讀。
- `ERROR_LOG.md` 是歷史紀錄，平時不用通讀；預防規則已經整理進 `.agents/rules/`。
- 只記非顯而易見、可能再犯的錯誤。由主 Agent（協調者）記錄，每筆都要同時補上 `.agents/rules/` 的預防規則與一個回歸測試。
- 里程碑完成或修復後，由協調者更新 `docs/progress.md`。

## 多 Agent 協作

需要「實作＋獨立驗證」時，主 Agent 依 `docs/workflow.md` 派工給 `implementer` 與 `verifier`（定義在 `.agents/agents/`）。被派出的 subagent 依自己的角色檔行事，不執行 `docs/workflow.md`。

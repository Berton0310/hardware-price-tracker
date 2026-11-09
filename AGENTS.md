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

## 指令（PowerShell）

這台機器有多個 Python，PATH 上的 `python` 是沒有安裝專案套件的系統 Python（ERR-018）。專案環境依 `start_project.bat` 的偵測順序：`.venv` → `venv` → `~/miniforge3` → `~/miniconda3` → `~/anaconda3`。目前是 `~/miniforge3`（Python 3.13）。下面的 `$PY` 指的就是它：

```powershell
$PY = "$env:USERPROFILE\miniforge3\python.exe"
& $PY -m pip install -r requirements.txt
& $PY -m playwright install chromium
& $PY -m pytest tests/ -v
& $PY -m pytest tests/test_crawlers.py -v
ruff check .
& $PY run_pipeline.py --help
```

## 寫程式的原則

- 只做當前需求要的功能。不預留抽象層、未使用的參數或用不到的防禦程式碼。
- 模組結構已經混亂時，重寫該模組比疊加補丁好，但要保持對外函式簽名不變、既有測試仍然通過。需要改簽名時先問使用者。
- 修改過的檔案要讓 `ruff check <檔案>` 沒有錯誤（未使用的 import／變數、`print`）。
- 模組對外公開的函式要標註參數與回傳型別。
- 網路請求一定設 `timeout`。
- 例外處理分兩層：
  - 函式內部只捕捉具體例外，例如 `requests.exceptions.RequestException`、Playwright 的 `Error`／`TimeoutError`、`ValueError`、`sqlite3.Error`。
  - 「邊界」可以捕捉 `Exception`，讓單一來源失敗不會拖垮整批：`services/` 裡每個資料來源的同步、CLI 入口、UI 的事件處理與資料載入、整次爬取。但一定要用 `logger.exception`／`logger.warning`（含錯誤訊息）記錄，或顯示給使用者，不能 `pass`。
  - 只有「失敗了也無所謂」的動作（例如設定 stdout 編碼、關閉彈窗）可以略過，而且仍然只捕捉具體例外。
- 價格：`used_asking_price` 欄位是 NOT NULL，`0` 代表「未開價」。分析與 UI 計算前一律剔除 `<= 0`（ERR-015）。新抓到的刊登解析不到價格時存 `0`；重新清洗既有資料時，不得把已有的價格覆蓋成 `0`（ERR-019）。
- 其他欄位解析失敗時，回傳 `None` 或跳過該筆，並用 `logging` 記下欄位與原始值。要特別防止真實價格被解析錯成 `0`（ERR-002）。
- 函式庫模組用 `logging`。只有 CLI 入口（`run_pipeline.py`、`login_fb.py`、`scripts/`）可以 `print`。
- 原價屋頁面用 `cp950` 解碼（cp950 是 Big5 的超集，用 `big5` 會遇到缺字）；其他檔案與模組一律 `utf-8`。

## 實際執行驗證

pytest 通過不代表程式真的能跑起來：測試用的是 mock，實際執行時才會碰到 Python 環境、編碼、工作目錄、換行符號這類問題（ERR-011、ERR-012、ERR-017、ERR-018）。所以只要改到「會被人直接執行」的東西，交付前就要實際跑一次並讀完輸出：

| 改到的東西 | 怎麼驗證 | 正常的標準 |
| :--- | :--- | :--- |
| CLI 腳本（`run_pipeline.py`、`scripts/*.py`、`login_fb.py`） | 用 `$PY` 執行一個安全的指令（`--help`，或指定暫存資料庫 `--db logs\smoke.db`） | exit code 為 0；輸出沒有 Traceback、亂碼或 `UnicodeEncodeError` |
| `.bat`、`.ps1` | 實際執行一次 | 顯示的 Python 路徑正確；沒有語法錯誤或中文亂碼 |
| Streamlit（`app/`） | 用下面的啟動腳本開起來，確認健康檢查回應後關掉 | `/_stcore/health` 回 `ok`、首頁回 200、stderr 沒有 Traceback |

```powershell
$PY = "$env:USERPROFILE\miniforge3\python.exe"
$env:HARDWARE_DB_PATH = "logs\smoke_app.db"   # 不碰 data/ 的正式資料庫
$p = Start-Process $PY -ArgumentList "-m","streamlit","run","app/main_app.py","--server.headless","true","--server.address","127.0.0.1","--server.port","8599" -PassThru -WindowStyle Hidden -RedirectStandardOutput logs\streamlit.out -RedirectStandardError logs\streamlit.err
$ok = $false
foreach ($i in 1..30) { Start-Sleep -Milliseconds 500; try { if ((Invoke-WebRequest http://127.0.0.1:8599/_stcore/health -UseBasicParsing -TimeoutSec 2).Content -eq "ok") { $ok = $true; break } } catch {} }
"health ok: $ok"
(Invoke-WebRequest http://127.0.0.1:8599/ -UseBasicParsing).StatusCode
Stop-Process -Id $p.Id -Force
Get-Content logs\streamlit.err
Remove-Item Env:HARDWARE_DB_PATH; Remove-Item logs\smoke_app.db*, logs\streamlit.* -ErrorAction SilentlyContinue
```

用 `127.0.0.1`，不要用 `localhost`：Windows 會先試 IPv6 的 `::1`，大約 2 秒後才退回 IPv4，健康檢查會一直逾時而誤判失敗。

- 健康檢查只能證明伺服器有起來。頁面腳本本身有沒有出錯，要靠 `tests/test_app.py` 的 `AppTest`，或用瀏覽器實際打開頁面確認。
- 開起來的服務驗證完一定要關掉，不要留在背景佔用連接埠。
- 輸出要真的讀過，不能只看 exit code：有些錯誤只會印警告，exit code 仍然是 0。
- 會連網爬取或寫入 `data/` 正式資料庫的指令（`--sync-*`、`--all`、`login_fb.py`），不能自己跑；要先取得使用者同意，或改用 `--db logs\smoke.db` 這類暫存資料庫。
- 在 pipe 裡截斷輸出（例如 `| Select-Object -First 3`）會讓 exit code 變成 -1。要看 exit code 時，先把輸出存進變數再看。
- 回報時附上實際執行的指令、exit code 與關鍵輸出。

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

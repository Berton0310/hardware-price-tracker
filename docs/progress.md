# 專案實作與驗證進度追蹤 (docs/progress.md)

本文件即時記錄專案各模組的開發狀態、驗證結果與待辦事項。

---

## 1. 全域里程碑總覽 (Milestone Overview)

| 階段 | 模組名稱 | 規劃文檔 | 實作狀態 | 驗證狀態 | 負責檔案 |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **M0** | 規範建置與環境準備 | `CLAUDE.md`, `AGENTS.md` | **DONE** | **PASS** | `CLAUDE.md`, `AGENTS.md`, `ERROR_LOG.md` |
| **M1** | 資料庫架構與 CRUD | `docs/modules/01_database.md` | **DONE** | **PASS** | `database/db_manager.py` |
| **M2** | 爬蟲模組與 ETL 清洗 | `docs/modules/02_crawlers.md` | **DONE** | **PASS** | `crawlers/coolpc_crawler.py`, `ptt_crawler.py`, `fb_crawler.py`, `cleaners/llm_parser.py`, `login_fb.py` |
| **M3** | 價格分析與統計演算法 | `docs/modules/03_analysis.md` | **DONE** | **PASS** | `analysis/price_analysis.py`, `tests/test_analysis.py` |
| **M4** | Streamlit 互動儀表板 | `docs/modules/04_app.md` | **DONE** | **PASS** | `app/main_app.py`, `app/__init__.py`, `tests/test_app.py` |
| **M5** | 端到端全系統整合測試 | `tests/test_integration.py` | **DONE** | **PASS** | `tests/test_integration.py`, `run_pipeline.py` |
| **M6** | 即時同步排程與控制中心 | `services/sync_service.py` | **DONE** | **PASS** | `services/sync_service.py`, `database/db_manager.py`, `app/main_app.py`, `tests/test_sync_feature_acceptance.py` |
| **M7** | Windows 每日排程自動同步 | `scripts/daily_sync.py` | **DONE** | **PASS** | `scripts/daily_sync.py`, `daily_crawl.bat`, `scripts/setup_schedule.bat`, `scripts/setup_schedule.ps1`, `run_pipeline.py` |

---

## 2. 當前進行中任務 (Active Task)
- **目前階段**：M7 Windows 每日排程自動同步驗收完成 (PASS)。
- **當前焦點**：已交付每日 20:00 Windows 工作排程器定時觸發腳本 (`daily_crawl.bat`, `scripts/daily_sync.py`)、一鍵管理腳本 (`scripts/setup_schedule.bat`, `scripts/setup_schedule.ps1`) 與 CLI 入口整合 (`run_pipeline.py --sync-daily`)，執行完畢乾淨關閉不佔用背景資源。全套 138 項測試全數 PASS。

---

## 3. 模組狀態細項追蹤

### [M1] 資料庫架構與 CRUD 模組
- [x] 撰寫 `docs/modules/01_database.md` 細部規格
- [x] 實作 `database/db_manager.py`（輕量 Python 內建 `sqlite3`，零 ORM 依賴）
- [x] 驗證 Agent 編寫 `tests/test_database.py`（16 項 In-Memory SQLite 完整驗收全數通過，0.12s）
- [x] 驗收評定：**PASS**

### [M2] 爬蟲模組與 ETL 清洗
- [x] 撰寫 `docs/modules/02_crawlers.md` 細部規格
- [x] 實作 `cleaners/llm_parser.py`（OpenRouter 免費模型支援 + 純 Regex 關鍵字 100% 離線 Fallback）
- [x] 實作原價屋新品爬蟲 `crawlers/coolpc_crawler.py`（4大核心類別、Big5編碼、漲跌標籤、入庫同步）
- [x] 實作 PTT HardwareSale 二手爬蟲 `crawlers/ptt_crawler.py`（over18=1、[賣/ 篩選、隨機禮貌延遲、貼文入庫）
- [x] 實作 FB 社團爬蟲 `crawlers/fb_crawler.py` 與互動式登入 `login_fb.py`（Cookie Pre-flight 健檢、真人捲動、無憑證公開降級）
- [x] 驗證 Agent 編寫 `tests/test_crawlers.py` 與 `tests/test_llm_parser.py`（Mock HTML 與極端測資斷言）
- [x] 修正 [ERR-003] 與 [ERR-004] 後全套測試通過（38/38 passed, 0.27s）
- [x] 驗收評定：**PASS**

### [M3] 價格分析演算法
- [x] 撰寫 `docs/modules/03_analysis.md` 細部規格
- [x] 實作 `analysis/price_analysis.py` 與 `analysis/__init__.py`（四大核心分析題目的指標與演算法，純 Python 零外部依賴）
- [x] 驗證 Agent 編寫擴充 `tests/test_analysis.py`（20 項邊界、極端數值、新品天花板防線與防除以零測資全數通過，0.44s）
- [x] 發現並修復 [ERR-005]：二手開價評定時新品 9 折天花板防線被低於行情優先匹配遮蔽之順序問題
- [x] 驗收評定：**PASS**

### [M4] Streamlit 介面與視覺化
- [x] 撰寫 `docs/modules/04_app.md` 細部規格
- [x] 實作 `app/__init__.py` 與 `app/main_app.py`（寬版配置、側邊欄分類/規格群組動態下拉、工作室/保固過濾開關、原價屋爬蟲即時同步按鈕）
- [x] 實作頂部 4 大指標卡（今日新品最低價附位階/真假降價、二手市場中位價、二手折數、轉買二手節省金額）
- [x] 實作中段 2 大 Plotly 視覺化圖表（新品 90 天走勢附高低均價參考線、跨平台與賣家類型價格對比直條圖）
- [x] 實作下方即時二手比價表格（四級行情標籤徽章、價差分析、新品折數、點擊連結）
- [x] 全面容錯與空狀態防護（空資料庫安全引導提示，AppTest 零崩潰驗收）
- [x] 驗證 Agent 編寫 `tests/test_app.py` 進行全方位自動化驗收（25 項無頭測試全數通過，5.58s）
- [x] 修正 [ERR-006]、[ERR-007] 與 [ERR-008]（跨大類規格洩漏、缺漏開價 TypeError、保固過濾漏抓終身保固）
- [x] 驗收評定：**PASS**

### [M5] 端到端全系統整合測試與全流程運行腳本
- [x] 實作 `run_pipeline.py`：極簡 CLI 介面（`--sync-coolpc`, `--sync-ptt`, `--analysis`, `--app`, `--all`）
- [x] 實作 `tests/test_integration.py`：端到端管線閉環、寫入冪等性、CLI 指令分派全自動化驗收
- [x] 驗證 Agent 與實作閉環（11 項整合測試全數通過，全專案 94/94 passed, 12.0s）
- [x] 發現並修復 [ERR-009] 與 [ERR-010]（群組命名未對齊斷鏈問題、賣家工作室判定詞庫寬鬆化）
- [x] 驗收評定：**PASS**

### [M6] 即時資料同步排程與控制中心
- [x] 實作 `database/db_manager.py`（新增 `sync_logs` 資料表結構、索引與 `record_sync_log`, `get_latest_sync_log`）
- [x] 實作 `services/sync_service.py`（整合原價屋新品、PTT 二手板、FB 社團依 Cookie 健康度判定優雅降級）
- [x] 實作 `app/main_app.py`（啟動 `st.session_state["auto_sync_done"]` 防重自動同步、側邊欄頂部同步控制中心 UI、立即同步互動響應）
- [x] 實作 `tests/test_sync_service.py` 單元測試（5 項測試全數通過）
- [x] 驗證 Agent 撰寫獨立驗收測試 `tests/test_sync_feature_acceptance.py`（13 項全流程與極端邊界測試全數通過）
- [x] 全套回歸測試驗證（127/127 passed, 20.75s）
- [x] 驗收評定：**PASS**

### [M7] Windows 每日排程自動同步與一鍵啟動
- [x] 實作 `scripts/daily_sync.py`（整合 `execute_sync`、支援 `--db`, `--ptt-pages`, `--fb-max-posts`, `--no-fb`, `--cookie-path`, `--log-dir` 參數、雙向寫入終端與 `logs/daily_sync.log`、標準退出碼）
- [x] 實作專案根目錄 `daily_crawl.bat`（Windows 工作排程器定時背景執行入口，執行完畢乾淨關閉，不常駐系統資源）
- [x] 實作專案根目錄 `start_project.bat`（一鍵啟動 Streamlit 儀表板，支援 Python 自動偵測、即時熱重載 `--server.runOnSave true` 與零 bytecode 滯留）
- [x] 實作 `scripts/setup_schedule.bat` 與 `scripts/setup_schedule.ps1`（排程註冊/更新、狀態查詢、排程移除一鍵管理工具，設定每日 20:00 執行）
- [x] 擴充 `run_pipeline.py` CLI 進入點（新增 `--sync-daily` 旗標與分派函式，`launch_dashboard` 啟用熱重載模式）
- [x] 驗證 Agent 編寫 `tests/test_daily_sync.py`（11 項參數解析、日誌建立、資料庫連線生命週期、批次檔語法與例外退出碼驗收全數通過）
- [x] 全專案回歸測試驗證（139/139 passed, 26.24s）
- [x] 驗收評定：**PASS**


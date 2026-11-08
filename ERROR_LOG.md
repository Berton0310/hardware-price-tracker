# ERROR_LOG.md - 實作與驗證錯誤追蹤與避坑知識庫

本文件是錯誤的歷史紀錄。各筆的預防規則已整理進 `.agents/rules/`（依模組自動載入），平時不需要通讀本檔。

- 只由主 Agent（協調者）記錄；只記非顯而易見、可能再犯的錯誤。
- 每記一筆，就要把預防規則補進 `.agents/rules/` 對應的檔案（標上 ERR 編號），並附上一個回歸測試。

---

## 錯誤登錄格式範本

```markdown
### [ERR-編號] 錯誤簡述標題
- **記錄日期**：YYYY-MM-DD
- **遭遇階段**：實作 Agent / 驗證 Agent / 整合測試
- **涉及檔案**：`path/to/file.py`
- **問題現象與報錯訊息**：
  ```text
  [貼上關鍵錯誤或 Stack Trace]
  ```
- **根本原因 (Root Cause)**：
  - 詳細說明為何發生此錯誤（如型態轉換未考慮空值、Regex 漏抓特定格式等）。
- **修復方案 (Resolution)**：
  - 具體採取了什麼修復或重寫策略。
- **預防規範 (Prevention Rule)**：
  - 未來在撰寫或驗收同類型程式碼時，必須遵守的防範準則。
- **已同步規則檔**：`.agents/rules/<模組>.md`
- **回歸測試**：`tests/test_<模組>.py::test_<名稱>`
```

---

## 錯誤歷史紀錄

### [ERR-001] pytest 執行時找不到 database 模組 (ModuleNotFoundError)
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 自動化測試配置
- **涉及檔案**：`pytest.ini`, `tests/test_database.py`
- **問題現象與報錯訊息**：
  ```text
  ModuleNotFoundError: No module named 'database'
  ```
- **根本原因 (Root Cause)**：
  - 在未設定 Python 模組搜尋路徑的環境下，直接調用 `pytest tests/` 不會自動將當前專案根目錄納入 `sys.path`。
- **修復方案 (Resolution)**：
  - 於專案根目錄建立 `pytest.ini`，設定 `pythonpath = .`，使 `pytest` 指令在任何子目錄皆能準確解析根目錄之所有模組。
- **預防規範 (Prevention Rule)**：
  - 新增專案設定或啟動測試時，一律確保 `pytest.ini` 定義好 `pythonpath`。

---

### [ERR-002] 原價屋單一價格選項被貪婪正則匹配截斷成 0 元
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / `coolpc_crawler.py`
- **涉及檔案**：`crawlers/coolpc_crawler.py`
- **問題現象與報錯訊息**：
  - 輸入 `, $9990 ◆ ★` 時，原本價格解析結果為 `listed_price: 0, current_price: 0`。
- **根本原因 (Root Cause)**：
  - 正則表達式 `r",\s*(\$?\d+)?\s*([↘↗])?\s*\$?(\d+)"` 中，前段 `(\$?\d+)?` 貪婪捕獲了前 3 位數 `$999`，強制迫使後段 `\$?(\d+)` 捕獲剩餘的個位數 `0`；而在無漲跌符號條件下，程式誤以非 None 的尾數作為價格，導致百位以上數字被吞噬。
- **修復方案 (Resolution)**：
  - 徹底重構價格抽取邏輯為 `_extract_prices_and_change`：
    1. 優先精準匹配雙價格包含箭頭語法 `r"\$?(\d+)\s*([↘↗])\s*\$?(\d+)"`（如 `$10500↘$9490`）。
    2. 若無雙價格，取逗號後單價格模式 `r"([↘↗])?\s*\$?(\d+)"`（如 `$9990` 或 `↘$8888`）。
- **預防規範 (Prevention Rule)**：
  - 處理價格字串時，嚴禁在同一個正則中連續放置兩個可匹配數字的正則群組；雙價格與單價格必須分流處理並編寫嚴格單元測資。

---

### [ERR-003] 二手貼文正則解析器誤採 dummy 假標價 ($99999) 且未辨別實際售價
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 自動化測試單元 `tests/test_llm_parser.py`
- **涉及檔案**：`cleaners/llm_parser.py`
- **問題現象與報錯訊息**：
  ```text
  AssertionError: assert 99999 == 9800
  where 99999 was extracted from "◎欲售價格：99999 (防呆勿下標，實際欲售 9800 元)"
  ```
- **根本原因 (Root Cause)**：
  - 在 `fallback_regex_parse` 中，價格抽取正則優先匹配 `◎欲售價格：` 之後的第一組數字 `([0-9,]{3,7})`，直接抓取到防呆標價 `99999`（或 `1`、`999999`），缺乏對常見防呆佔位符（如 99999, 88888, 123456）與接續「實售/實際欲售」關鍵字的辨識與剔除邏輯。
- **修復方案 (Resolution)**：
  - 在價格解析邏輯中加入假標價/防呆標價過濾；若數值為 99999, 999999, 88888 或低於 50 元，或後方伴隨「實售/實際/底標」字樣，應自動搜尋內文中之真實金額。
- **預防規範 (Prevention Rule)**：
  - 二手交易貼文常見賣家設定假標價規避系統限制或防呆，抽取欲售金額時必須加入異常高/低防呆數字黑名單檢查並優先抓取實售修飾語。

---

### [ERR-004] 二手貼文正則解析器遺漏終身保固 (終保) 識別導致誤判為過保
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 自動化測試單元 `tests/test_llm_parser.py`
- **涉及檔案**：`cleaners/llm_parser.py`
- **問題現象與報錯訊息**：
  ```text
  AssertionError: assert '過保' == '終保'
  where '過保' was returned for content containing "捷元代理終身保固，盒裝完整功能正常"
  ```
- **根本原因 (Root Cause)**：
  - 保固狀態正則 `(保固內|保內|原廠保|保固至|註冊[四五]年保|保固中)` 未涵蓋記憶體（RAM）與 SSD 最核心的「終身保固」、「終身保」、「終保」關鍵字，導致所有終身保固商品因未命中保內而跌入 `else: warranty_status = "過保"`。
- **修復方案 (Resolution)**：
  - 於保固狀態判定中優先新增 `(終身保固|終身保|終保)` 分支，並明確賦予 `warranty_status = "終保"`。
- **預防規範 (Prevention Rule)**：
  - 電腦硬體各類別保固生態不同，RAM 常態為終保，保固狀態分類必須涵蓋「原廠保內」、「過保」、「終保」，嚴禁遺漏。

---

### [ERR-005] 二手開價評定時新品天花板防線被低於行情優先匹配遮蔽
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 自動化測試單元 `tests/test_analysis.py`
- **涉及檔案**：`analysis/price_analysis.py`
- **問題現象與報錯訊息**：
  - 當二手市場整體開價虛高（例如二手已售出中位數為 10,000 元），而新品市場價格大幅跳水（例如最低新品價降至 8,000 元，9 折天花板為 7,200 元）時，若二手賣家開價 7,500 元：
  - 由於 7,500 < 9,000（中位數 9 折），原程式碼將 `elif p_ask < benchmark_price * 0.9:` 置於 `elif p_ask >= new_min_price * 0.9:` 之前，導致 7,500 元被誤判為 `bargain`（「低於行情 (推薦購買)」），遮蔽了新品天花板防線，未給出 `overpriced`（「偏貴 (建議買新品)」）。
- **根本原因 (Root Cause)**：
  - 多條件分級判定時條件順序錯誤。在硬體比價邏輯中，「建議買新品（開價達新品 9 折以上）」屬於全局天花板最高防線；若優先判定二手折數，將在二手行情虛高或新品崩跌時，對接近新品價格的二手開價給出錯誤的「推薦購買」結論。
- **修復方案 (Resolution)**：
  - 調整 `evaluate_listing_price` 判定順序：
    1. 最優先判定 `anomaly_low`（< 50% 二手均價，防詐騙/故障）。
    2. 次優先判定 `overpriced`（> 110% 二手均價 或 >= 90% 新品最低價，天花板攔截）。
    3. 再判定 `bargain`（< 90% 二手均價，低於行情推薦）。
    4. 最後落在 `fair`（符合行情）。
- **預防規範 (Prevention Rule)**：
  - 涉及到多基準（二手基準 vs. 新品天花板）的多層條件判定，絕對優先級天花板條件必須置於次級相對折扣條件之前；驗收時必須建構「二手基準反超新品價格」之極端測資進行覆蓋測試。

---

### [ERR-006] get_spec_groups 跨大類規格洩漏 (OR u.spec_group IS NOT NULL 導致全域混雜)
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 儀表板資料查詢與介面聯動驗證
- **涉及檔案**：`app/main_app.py`
- **問題現象與報錯訊息**：
  - 當資料庫中僅有 GPU 類別之產品主檔與二手貼文，切換至 RAM 類別查詢時，`get_spec_groups(conn, 'RAM')` 竟然回傳 GPU 規格群組（如 `['RTX 4060 Ti 16G']`），導致側邊欄類別選單與規格群組選單不一致。
- **根本原因 (Root Cause)**：
  - `get_spec_groups` 之備用查詢語法為 `WHERE (p.category = ? OR u.spec_group IS NOT NULL)`，由於 `u.spec_group IS NOT NULL` 對於所有刊登均為真，使得 `OR` 運算式短路成立，意外撈出全資料庫跨所有類別之所有規格。
- **修復方案 (Resolution)**：
  - 將備用查詢調整為嚴格匹配 `JOIN products p ON u.product_id = p.product_id WHERE p.category = ? AND u.spec_group IS NOT NULL`；若該類別無任何匹配產品或貼文，則安全回退至該類別預設規格清單 `DEFAULT_SPEC_GROUPS.get(category)`。
- **預防規範 (Prevention Rule)**：
  - SQL 聯表篩選類別時，嚴禁於 `WHERE` 子句中使用寬鬆的非空判斷作為 `OR` 替代分支，避免跨大類資料混淆；驗收時必須建立單一大類資料庫並對其他所有大類執行隔離測試。

---

### [ERR-007] build_listings_dataframe 遭遇缺失或空開價時拋出 TypeError 崩潰
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 二手比價表格髒資料容錯驗證
- **涉及檔案**：`app/main_app.py`
- **問題現象與報錯訊息**：
  ```text
  TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'
  ```
- **根本原因 (Root Cause)**：
  - `build_listings_dataframe` 遍歷列表時直接執行 `asking = int(r["used_asking_price"])`，若貼文記錄中的 `used_asking_price` 為 `None` 或欄位缺漏，即刻引發未捕獲之 `TypeError` 導致整個 Streamlit 表格崩潰。
- **修復方案 (Resolution)**：
  - 強化空值防護為 `asking = int(r.get("used_asking_price") or 0)`，確保在面對不完整或髒測資時皆能平穩運作不中斷。
- **預防規範 (Prevention Rule)**：
  - 前端 UI 表格渲染模組嚴禁直接以索引 `dict[...]` 強制型別轉換；必須使用 `.get(key, default)` 提供安全預設值，並於單元測試中覆蓋包含 `None` 與缺欄位的邊界測資。

---

### [ERR-008] 側邊欄僅看保固內開關遺漏終身保固 (終身) 關鍵字識別導致記憶體/SSD 被錯誤濾除
- **記錄日期**：2026-11-03
- **遭遇階段**：驗證 Agent / 二手列表篩選開關驗證
- **涉及檔案**：`app/main_app.py`
- **問題現象與報錯訊息**：
  - 測試案例 `test_filter_listings_only_in_warranty` 斷言應包含 2 筆保內商品，實際僅回傳 1 筆。標註為「終身保固」之記憶體刊登被 `only_in_warranty=True` 錯誤過濾剔除。
- **根本原因 (Root Cause)**：
  - `get_filtered_listings` 內部保固判斷僅包含 `"保內" in warranty or "終保" in warranty or "原廠保" in warranty`，遺漏了「終身保固」字串中未縮寫之「終身」與「保固中」關鍵字，導致標示「終身保固」之合法有效商品被排除。
- **修復方案 (Resolution)**：
  - 擴充保固判斷關鍵字：加入 `"終身" in warranty` 與 `"保固中" in warranty`，全面相容完整描述與縮寫。
- **預防規範 (Prevention Rule)**：
  - 前端與資料庫各層過濾規則必須與清洗層 `llm_parser` 同步相容硬體專屬名詞（如記憶體終保）；單元測試必須包含完整「終身保固」真實字串測資。

---

### [ERR-009] 原價屋爬蟲與二手清洗層 spec_group 命名標準未對齊導致比價斷鏈
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 端到端全系統整合測試 `tests/test_integration.py`
- **涉及檔案**：`crawlers/coolpc_crawler.py`, `cleaners/llm_parser.py`, `analysis/price_analysis.py`
- **問題現象與報錯訊息**：
  ```text
  AssertionError: assert None == 14490
  where None was returned from calculate_spec_group_discount(conn, "RTX 4060 Ti 16G")
  ```
- **根本原因 (Root Cause)**：
  1. 原價屋爬蟲自 HTML optgroup 抽取之文字為 `RTX4060Ti-16GB`，原始正則僅抽換破折號為空格，產出 `RTX4060Ti 16GB`（無空格且後綴為 GB）。
  2. 二手清洗層 `llm_parser` 抽取產出 `RTX 4060 TI 16G`，而前端預設標準為 `RTX 4060 Ti 16G`。
  3. SQL 查詢 `WHERE p.spec_group = ?` 進行精確字串比對，導致新品主檔與二手刊登群組名無法相連，計算新品最低價回傳 `None`。
- **修復方案 (Resolution)**：
  1. 於 `coolpc_crawler.py` 之 `_clean_spec_group` 加入規範化補空與後綴過濾（如 `RTX4060Ti-16GB` -> `RTX 4060 Ti 16G`，`32GB DDR5-6000` -> `DDR5-6000 32G`）。
  2. 於 `llm_parser.py` 加入大小寫標準化（`Ti`、`SUPER`、`G`）。
  3. 於 `analysis/price_analysis.py` 所有規格查詢子句加上 `COLLATE NOCASE`，提供對大小寫差異的免疫韌性。
- **預防規範 (Prevention Rule)**：
  - 各爬蟲與清洗層輸出之核心枚舉/群組欄位（如 `spec_group`、`category`）必須具有全域統一的規範化標準；資料庫查詢對於使用者輸入或多來源群組名稱，應搭配 `COLLATE NOCASE` 預防大小寫不一致。

---

### [ERR-010] 賣家類型篩選器判定詞庫不完整導致 "二手工作室" 未命中工作室分類
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 端到端全系統整合測試 `tests/test_integration.py`
- **涉及檔案**：`analysis/price_analysis.py`, `app/main_app.py`
- **問題現象與報錯訊息**：
  ```text
  AssertionError: assert 0 >= 1
  where 0 was comp["groups"]["FB_Group_workshop"]["count"]
  ```
- **根本原因 (Root Cause)**：
  - 商業工作室判定使用白名單包含性比對：`st in ("工作室", "商業工作室", "workshop")`，當賣家型態被標示為 `"二手工作室"` 或 `"工作室/店家"` 時無法匹配，被誤歸類為個人自售。
- **修復方案 (Resolution)**：
  - 重構為寬鬆子字串模式：`("工作室" in st) or ("店家" in st) or ("workshop" in st.lower())`，全面相容各種中文賣家型態組合。
- **預防規範 (Prevention Rule)**：
  - 對於類別型字串標籤的判讀，應優先使用特徵詞包含性判斷（`in`）配合關鍵字子字串，避免過於嚴格的固定元組相等比對。

---

### [ERR-011] 直接執行 `streamlit run app/main_app.py` 導致 ModuleNotFoundError: No module named 'analysis'
- **記錄日期**：2026-11-03
- **遭遇階段**：使用者實際執行階段 (Runtime Execution)
- **涉及檔案**：`app/main_app.py`, `run_pipeline.py`
- **問題現象與報錯訊息**：
  ```text
  ModuleNotFoundError: No module named 'analysis'

  File "C:\Users\berto\Desktop\my\learning\web crawler\midterm_project\app\main_app.py", line 17, in <module>
      from analysis.price_analysis import (
  ```
- **根本原因 (Root Cause)**：
  - 當直接使用 `streamlit run app/main_app.py` 啟動時，Python 會自動將腳本所在的子目錄 `app/` 設為 `sys.path[0]`，而未將專案根目錄納入模組搜尋路徑。
  - 先前 `pytest` 能順利通過是因為 `pytest.ini` 中已設定 `pythonpath = .`，因此掩蓋了單獨執行 `app/main_app.py` 時的跨目錄 import 缺陷。
- **修復方案 (Resolution)**：
  - 在 `app/main_app.py` 與 `run_pipeline.py` 最頂部顯式加入動態根目錄解析：
    ```python
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    ```
- **預防規範 (Prevention Rule)**：
  - 子目錄下的應用入口腳本（如 Streamlit、Flask、FastAPI 入口），嚴禁假設執行當下的 Working Directory 必然在專案根目錄，必須於腳本頭部使用 `Path(__file__).resolve().parent...` 自主鎖定專案根目錄並寫入 `sys.path`。

---

### [ERR-012] Windows cp950 終端印出特殊 Unicode 符號引發 UnicodeEncodeError
- **記錄日期**：2026-11-03
- **遭遇階段**：實機爬蟲腳本執行 (`scripts/crawl_fb.py`)
- **涉及檔案**：`scripts/crawl_fb.py`
- **問題現象與報錯訊息**：
  ```text
  UnicodeEncodeError: 'cp950' codec can't encode character '\u2713' in position 0: illegal multibyte sequence
  ```
- **根本原因 (Root Cause)**：
  - Windows 控制台終端預設編碼為 `cp950`，在印出 `\u2713` (✓) 或 `\u2717` (✗) 等特殊符號時，若未重設 stdout 編碼，Python 會直接拋出編碼失敗異常並終止腳本。
- **修復方案 (Resolution)**：
  - 於 CLI 腳本頂部強制配置 UTF-8 輸出：`sys.stdout.reconfigure(encoding="utf-8")`，並使用安全純 ASCII 標籤如 `[OK]` 與 `[FAIL]`。
- **預防規範 (Prevention Rule)**：
  - 在 Windows 執行環境下的 Python CLI 腳本，禁止無保護地直接在 print 中使用非 cp950 字符，或必須顯式加入 `reconfigure(encoding="utf-8")`。

---

### [ERR-013] OpenRouter 舊實驗模型 404 失效與 Fallback 解析器抓取 PTT/FB 介面噪音標籤 (`[賣` / `Facebook`)
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 二手貼文重解析與前端聯動
- **涉及檔案**：`cleaners/llm_parser.py`, `crawlers/fb_crawler.py`, `.env`
- **問題現象與報錯訊息**：
  - 二手資料庫中出現 21 筆 `spec_group` 為 `[賣`、13 筆為 `Facebook`，且價格解析為 0 元。
  - 後端呼叫 OpenRouter 時回傳 HTTP 404，模型 `google/gemini-2.0-flash-exp:free` 已被服務商下架廢棄。
- **根本原因 (Root Cause)**：
  1. OpenRouter 免費實驗模型已過期失效，請求失敗跌入正則備援。
  2. Fallback 正規表達式直接切割字串並取首詞；PTT 標題多以 `[賣/雙北/面交]` 開頭，切詞首詞為 `[賣`。FB 爬蟲則抓取到頁面導航 UI 文字 `Facebook` 與 `社團動態消息排序方式`。
  3. 價格正則僅支援單純 `◎欲售價格： 12000`，面對自然語言如 `1~5合售 8000`、`1. 8000`、`5.3萬` 等皆解析失敗回傳 0。
- **修復方案 (Resolution)**：
  1. 更新模型為現行穩定可用之 `google/gemini-2.5-flash`。
  2. 新增 `_clean_title_text` 與 `EXCLUDE_WORDS` 過濾中括號與交易雜訊；RAM 限制容量正則在 4~256G，避免 512GB SSD 串味。
  3. 支援萬字轉換 (`5.3萬` -> `53000`)、合售、序號式售價解析。
- **預防規範 (Prevention Rule)**：
  - 外部 LLM API 模型名稱應定期檢查可用性；Fallback 解析器必須具備標題淨化機制，嚴防方括號與社群平臺標題進入規格標籤。

---

### [ERR-014] 原價屋顯卡分類爬蟲納入轉接線周邊配件導致新品最低價被 $190 污染
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 原價屋爬蟲與比價分析
- **涉及檔案**：`crawlers/coolpc_crawler.py`, `cleaners/resanitize_db.py`
- **問題現象與報錯訊息**：
  - 儀表板 GPU 類別顯示最低新品價為 $190，對應商品為「麗臺 mini-DP 轉接線」，導致所有二手顯卡比價折數計算嚴重失真。
- **根本原因 (Root Cause)**：
  - 原價屋線上估價系統將「轉接線材」、「顯卡支架」等配件放置於「顯示卡」大分類群組內；爬蟲未對品名關鍵字及異常低價（< $500）進行防禦性過濾。
- **修復方案 (Resolution)**：
  - 在 `coolpc_crawler.py` 的 GPU 解析中，過濾 `轉接線|轉接頭|轉接器|轉接|支架|排線|延長線|散熱片|散熱貼` 等關鍵字，並排除新品價格低於 500 元之非本體配件。
  - 編寫 `cleaners/resanitize_db.py` 刪除資料庫中殘存之 1 筆轉接線產品記錄。
- **預防規範 (Prevention Rule)**：
  - 新品硬體爬蟲解析時，針對核心硬體（GPU、CPU、主機板）必須設定合理最低價格底線與配件關鍵字黑名單，避免非核心配件污染主檔最低價。

---

### [ERR-015] 二手市場刊登價格為 0/未開價計入中位數拉低行情，且新品無報價時前端折數指標破版
- **記錄日期**：2026-11-03
- **遭遇階段**：實作 Agent / 價格分析計算與 Streamlit 儀表板呈現
- **涉及檔案**：`analysis/price_analysis.py`, `app/main_app.py`
- **問題現象與報錯訊息**：
  - 部分二手貼文為「徵求」或「洽中未開價」（價格為 0），被計入二手已售出中位數與個人自售價格，導致中位數被拉低至不可信之數值。
  - 當特定二手規格群組在原價屋已無在售新品時，新品最低價為 None/0，前端指標卡仍嘗試顯示折扣率，產生混亂或無意義資訊。
- **修復方案 (Resolution)**：
  - 在 `calculate_spec_group_discount`、`compare_platforms_and_sellers` 與 `evaluate_listing_price` 中，統計二手價格前全面排除 `used_asking_price <= 0`。
  - 升級 `format_metric_card_data`：當新品在售為 0/None 時，明確標示「原價屋目前無新品在售」，並清晰展示二手市場行情與在售筆數，不顯示無意義的「折數」與「省下金額」。
- **預防規範 (Prevention Rule)**：
  - 價格運算前必須嚴格剔除無效值（<= 0）；前端指標呈現必須兼顧「新品斷貨/無新品」的常見二手市場情境，給予友善替代提示。

---

### [ERR-016] Windows 排程腳本中文編碼與 CMD/PowerShell 括號解析衝突
- **記錄日期**：2026-11-08
- **遭遇階段**：實作 Agent / Windows 排程腳本開發 (`setup_schedule.bat`, `setup_schedule.ps1`)
- **涉及檔案**：`scripts/setup_schedule.bat`, `scripts/setup_schedule.ps1`, `daily_crawl.bat`
- **問題現象與報錯訊息**：
  - PowerShell 5.1 執行時報錯：`Unexpected token '}' in expression or statement.`
  - CMD 執行 `.bat` 時報錯：`'尚未註冊' is not recognized as an internal or external command`，或誤將 UTF-8 中文中的字節當成括號/指令連接符。
- **根本原因 (Root Cause)**：
  1. Windows 內建 PowerShell 5.1 對無 BOM 的 UTF-8 檔案預設採用系統 ANSI (CP950) 解譯，繁體中文特定字元經解碼後字節剛好撞上花括號或引號，導致 AST 解析崩潰。
  2. CMD 批次檔若採用多行 `if (...) ( ... )` 括號區塊，在非 ANSI 或含有特殊繁體中文字元（如第二 byte 為 `0x5C` 的「功」、「或」等字）時，括號配對會被提前截斷。
- **修復方案 (Resolution)**：
  1. `setup_schedule.ps1` 一律儲存為帶 BOM 之 UTF-8 (`utf-8-sig`)，確保 PowerShell 5.1 跨平台版本均能精準識別。
  2. `daily_crawl.bat` 採用純 ASCII 英文指令與明確 `chcp 65001 > nul`，避免 `@echo off` 前附帶 BOM 導致 CMD 報錯。
  3. `setup_schedule.bat` 捨棄巢狀括號區塊 `if (...) ( ... )`，全面改用單行條件判斷搭配標籤跳轉 (`goto`)，徹底杜絕字元匹配錯誤。
- **預防規範 (Prevention Rule)**：
  - Windows 原生批次檔 (`.bat`) 與 PowerShell 腳本 (`.ps1`) 包含非英文字元時，必須特別防範 ANSI/UTF-8 BOM 相容性；批次檔邏輯分支應優先採用 `goto` 而非多行括號塊。

---

### [ERR-017] Windows 批次檔換行符號為 LF 導致 CMD 語法解析中斷與 echo 括號破壞
- **記錄日期**：2026-11-08
- **遭遇階段**：實作 Agent / 一鍵啟動批次檔 (`start_project.bat`)
- **涉及檔案**：`start_project.bat`
- **問題現象與報錯訊息**：
  ```text
  '65001' is not recognized as an internal or external command
  '何程式碼並存檔，網頁將自動即時更新)' is not recognized as an internal or external command
  ```
- **根本原因 (Root Cause)**：
  1. 編輯器以 Unix 換行符 (`\n`, LF) 寫入 `.bat` 檔，Windows CMD 批次直譯器需要 Windows 換行符 (`\r\n`, CRLF)，否則會將多行指令拼湊為單行解析。
  2. `echo` 語句中包含未加引號的括號 `(...)`，在 CMD 中會被誤識別為區塊結尾符。
- **修復方案 (Resolution)**：
  1. 使用腳本全面標準化 `.bat` 換行符為 `\r\n` (CRLF)。
  2. 避免在無引號的 `echo` 中放置括號，改用連字號 `-` 或安全純英數字元串。
- **預防規範 (Prevention Rule)**：
  - 所有產出的 `.bat` 檔案均必須保證為 CRLF 換行，且 echo 提示文字避免使用未跳脫之特殊符號（如 `(`, `)`, `%`, `^`）。

---

### [ERR-018] Windows 多版本 Python 導致雙擊批次檔解析至無套件之系統 Python
- **記錄日期**：2026-11-08
- **遭遇階段**：實作 Agent / 批次檔執行 (`start_project.bat`, `daily_crawl.bat`)
- **涉及檔案**：`start_project.bat`, `daily_crawl.bat`
- **問題現象與報錯訊息**：
  ```text
  C:\Users\berto\AppData\Local\Programs\Python\Python312\python.exe: No module named streamlit
  ```
- **根本原因 (Root Cause)**：
  - 使用者主機同時安裝了多個 Python 版本（Miniforge3、Python 3.12、3.10、3.6 等）。
  - 在檔案總管中雙擊 `.bat` 檔案時，Windows 的 PATH 預設優先順序指向系統安裝的 `Python312`，而未指向專案依賴所安裝的 `miniforge3` 環境。
- **修復方案 (Resolution)**：
  - 升級 `.bat` 中的 Python 偵測機制：依序檢查專案內 `.venv`、`venv`，接著主動檢查使用者目錄之 `miniforge3`、`miniconda3`、`anaconda3` 絕對路徑，最後才回退至 PATH 中的 `python`。
  - 在啟動 Streamlit 前，先以 `"%PYTHON_EXE%" -c "import streamlit"` 進行 Pre-flight 檢查，若無套件給予明確中文診斷提示。
- **預防規範 (Prevention Rule)**：
  - Windows 環境下的 `.bat` 批次檔不可僅使用單純的 `python` 指令，必須實作 Conda/Miniforge/Virtualenv 多層級環境自動偵測與套件可用性驗證。




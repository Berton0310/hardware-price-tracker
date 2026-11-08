# 模組規格：爬蟲與資料擷取 (docs/modules/02_crawlers.md)

本文件定義爬蟲模組（`crawlers/`）之實作規格與驗收標準。

---

## 1. 模組架構與檔案清單
- `crawlers/coolpc_crawler.py`：原價屋新品線上估價單爬蟲（靜態 HTML，Big5 編碼）
- `cleaners/llm_parser.py`：OpenRouter LLM 貼文自然語言結構化解析模組
- `crawlers/ptt_crawler.py`：PTT HardwareSale 二手板爬蟲（靜態 HTML，分頁）
- `crawlers/fb_crawler.py`：Facebook 二手社團動態爬蟲（Playwright 模擬真人）
- `login_fb.py`：Facebook 互動登入與 Cookie 導出小工具

---

## 2. 細部規格

### 2.1 原價屋爬蟲 (`crawlers/coolpc_crawler.py`)
- **目標網址**：`https://www.coolpc.com.tw/evaluate.php`
- **編碼**：`cp950` / `Big5`
- **抓取類別**：
  - GPU: 類別 12 (顯示卡)
  - RAM: 類別 6 (記憶體)
  - SSD: 類別 7 (M.2/SSD)
  - CPU: 類別 4 (處理器)
- **抽取欄位**：
  - `spec_group`：由 `<optgroup label="...">` 抽取（如 `RTX 4060 Ti 16G`、`DDR5-6000 32G`）
  - `model_name`：由 `<option>` 抽取去除多餘符號之完整型號
  - `brand`：抽取廠牌（如 `華碩`, `微星`, `技嘉` 等，統一標準化英文如 `ASUS`, `MSI`, `Gigabyte`）
  - `listed_price` & `current_price`：原價與特價（支援 `↘$14990` 降價語法）
  - `change_tag`：`'↘降價'`, `'↗漲價'`, `'不變'`
- **入庫函式**：直接呼叫 `db_manager.upsert_product` 與 `db_manager.record_new_price`。

### 2.2 OpenRouter LLM 貼文解析器 (`cleaners/llm_parser.py`)
- **接口**：`parse_post_with_llm(post_title: str, post_content: str) -> dict`
- **API 調用**：OpenRouter 免費模型（`google/gemini-2.0-flash-exp:free`）。
- **回傳 Schema**：
  `category`, `spec_group`, `brand`, `model_name`, `used_asking_price`, `warranty_status`, `seller_type`, `trade_method`。
- **Fallback 機制**：若無 API Key 或網路異常，自動使用正則備用語法抽取，確保程式不拋錯崩潰。

### 2.3 PTT 爬蟲 (`crawlers/ptt_crawler.py`)
- **目標網址**：`https://www.ptt.cc/bbs/HardwareSale/index.html`
- **Cookie**：`over18=1`
- **過濾條件**：標題含 `[賣/`，剔除 `[徵/`。
- **禮貌延遲**：翻頁間隔加入 `1.2 ~ 2.0 秒` 隨機延遲。
- **流程**：解析列表 $\rightarrow$ 取得內文 $\rightarrow$ 呼叫 `llm_parser` $\rightarrow$ 呼叫 `db_manager.upsert_used_listing`。

### 2.4 Facebook 爬蟲 (`crawlers/fb_crawler.py` & `login_fb.py`)
- **Cookie 健康檢查 (Pre-flight)**：
  - 檢查 `data/fb_cookies.json`。
  - 載入後驗證是否為有效登入狀態。
  - 若失效，發出警告並切換為「公開模式」爬取首頁貼文。
- **真人生動捲動**：每次捲動 600~900px，隨機等待 2.5 ~ 4.5 秒。

---

## 3. 驗收標準 (Tests)
- `tests/test_crawlers.py`：使用本地 Mock HTML 片段（離線測試），驗證原價屋與 PTT 的解析正確性。
- `tests/test_llm_parser.py`：驗證 Fallback 正則抽取與結構化輸出有效性。

# 模組規格：資料庫設計與 CRUD (docs/modules/01_database.md)

本文件定義資料庫模組（`database/`）之實作規格與驗收標準。
依照「刪到不能再刪」標準，本模組**揚棄 SQLAlchemy ORM**，直接採用 Python 內建之 `sqlite3` 標準函式庫，零外部依賴。

---

## 1. 實作範疇
* **核心檔案**：`database/db_manager.py`（單一檔案管理連線、建表、UPSERT 與查詢）
* **測試檔案**：`tests/test_database.py`
* **資料庫路徑**：`data/hardware.db`（支援 `:memory:` 測試模式）

---

## 2. 資料表結構定義 (SQLite DDL)

### 2.1 `products`（硬體標準主檔）
```sql
CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,         -- 例如: 'GPU', 'RAM', 'SSD', 'CPU'
    spec_group TEXT NOT NULL,       -- 例如: 'RTX 4060 Ti 16G' (聚合分析核心鍵)
    brand TEXT NOT NULL,            -- 例如: 'ASUS', 'MSI', 'Gigabyte'
    model_name TEXT NOT NULL UNIQUE,-- 完整標準型號
    key_specs TEXT,                 -- 核心規格摘要
    warranty_years INTEGER DEFAULT 3
);
CREATE INDEX IF NOT EXISTS idx_products_spec_group ON products(spec_group);
```

### 2.2 `new_price_history`（新品每日歷史價格表）
```sql
CREATE TABLE IF NOT EXISTS new_price_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    record_date TEXT NOT NULL,      -- 'YYYY-MM-DD'
    listed_original_price INTEGER NOT NULL,
    current_new_price INTEGER NOT NULL,
    price_change_tag TEXT NOT NULL, -- '不變', '↘降價', '↗漲價'
    in_stock INTEGER DEFAULT 1,     -- 1: 有貨, 0: 缺貨
    UNIQUE(product_id, record_date)
);
CREATE INDEX IF NOT EXISTS idx_price_date ON new_price_history(record_date);
```

### 2.3 `used_market_listings`（二手市場貼文與行情追蹤表）
```sql
CREATE TABLE IF NOT EXISTS used_market_listings (
    post_id TEXT PRIMARY KEY,       -- 例如: 'FB_9823412', 'PTT_M.173051.A'
    product_id INTEGER REFERENCES products(product_id) ON DELETE SET NULL,
    spec_group TEXT NOT NULL,       -- 對應之標準規格群組
    source_platform TEXT NOT NULL,  -- 'FB_Group' 或 'PTT'
    seller_name TEXT NOT NULL,
    seller_type TEXT NOT NULL,      -- '個人自售' 或 '商業工作室'
    used_asking_price INTEGER NOT NULL,
    warranty_status TEXT NOT NULL,  -- '原廠保內', '終身保固', '過保'
    trade_method TEXT,
    comment_count INTEGER DEFAULT 0,
    sale_status TEXT NOT NULL,      -- '販售中', '已售出'
    post_time TEXT NOT NULL,        -- ISO 8601 或 'YYYY-MM-DD HH:MM:SS'
    post_url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_used_spec_group ON used_market_listings(spec_group);
CREATE INDEX IF NOT EXISTS idx_used_post_time ON used_market_listings(post_time);
```

---

## 3. `database/db_manager.py` 函式介面規範 (刪到不能再刪)

提供乾淨、純函式/輕量類別介面：
1. `init_db(db_path: str = "data/hardware.db") -> sqlite3.Connection`
   - 自動開啟外鍵支援 (`PRAGMA foreign_keys = ON;`) 與 WAL 模式。
   - 建立上述三張表格與索引。
2. `upsert_product(conn, category, spec_group, brand, model_name, key_specs="", warranty_years=3) -> int`
   - 插入或忽略，回傳 `product_id`。
3. `record_new_price(conn, product_id, record_date, listed_price, current_price, change_tag="不變", in_stock=True)`
   - 使用 `INSERT INTO new_price_history ... ON CONFLICT(product_id, record_date) DO UPDATE SET current_new_price=...` 冪等寫入。
4. `upsert_used_listing(conn, post_dict: dict)`
   - 使用 `INSERT INTO used_market_listings ... ON CONFLICT(post_id) DO UPDATE SET used_asking_price=..., sale_status=...` 冪等寫入。
5. `get_product_price_history(conn, product_id: int, days: int = 90) -> list[dict]`
   - 查詢某產品近 N 日價格軌跡。
6. `get_used_market_by_spec(conn, spec_group: str) -> list[dict]`
   - 查詢某規格群組之二手貼文列表。

---

## 4. 驗收標準 (Tests)
* 由獨立驗證 Agent 在 `tests/test_database.py` 中以 `:memory:` 記憶體資料庫全自動測試。
* 涵蓋：外鍵約束、唯一索引防重複、覆蓋更新 (UPSERT) 正確性、關聯查詢效能。

---
trigger: glob
globs: "app/**"
---

# Streamlit 介面規則

- 入口腳本開頭用 `Path(__file__).resolve().parent.parent` 把專案根目錄加進 `sys.path`，不要假設工作目錄在專案根目錄（ERR-011）。
- 讀資料列一律用 `.get(key, default)`，不要直接 `row["x"]` 再轉型；缺欄位或 `None` 不能讓表格崩潰（ERR-007）。
- 類別判斷用關鍵字包含（`in`），不要用固定 tuple 的相等比對（ERR-010）；保固篩選要涵蓋「終保／終身」（ERR-008）。
- 新品沒有報價時顯示替代提示，不顯示錯誤或 0 折（ERR-015）。
- App 的資料庫路徑預設是正式的 `data/hardware.db`；測試（含 AppTest）一定要用 `HARDWARE_DB_PATH` 指到 tmp_path（ERR-019）。
- 不在 UI 裡寫 SQL 或呼叫爬蟲；需要資料時呼叫 `database/`、`analysis/` 的函式。

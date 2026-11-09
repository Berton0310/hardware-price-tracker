# 文件索引

這個目錄放專案的進度、協作流程與各模組規格。開工前先讀 `progress.md`，再只讀與任務相關的模組文件。

| 文件 | 內容 | 何時讀 |
| :--- | :--- | :--- |
| [progress.md](progress.md) | 里程碑、各模組實作與驗證狀態 | 每次開工前 |
| [workflow.md](workflow.md) | 主 Agent 派工給 implementer／verifier 的流程 | 需要「實作＋獨立驗證」時（主 Agent） |
| [modules/01_database.md](modules/01_database.md) | SQLite（`sqlite3`）表結構、欄位、約束、CRUD 規格 | 修改或驗證 `database/` |
| [modules/02_crawlers.md](modules/02_crawlers.md) | 原價屋、PTT、FB 爬蟲目標、Regex 清洗與型號群組 | 修改或驗證 `crawlers/` |
| [modules/03_analysis.md](modules/03_analysis.md) | 歷史百分位、二手折數、跨平台比價、異常低價公式 | 修改或驗證 `analysis/` |
| [modules/04_app.md](modules/04_app.md) | Streamlit 版面、Plotly 圖表、篩選與表格 | 修改或驗證 `app/` |
| [modules/05_llm_parser.md](modules/05_llm_parser.md) | OpenRouter 模型呼叫、Prompt 與貼文解析規格 | 修改或驗證 `cleaners/` |

# 專案進度與漸進式架構導引 (docs/index.md)

本目錄存放專案之進度追蹤、架構規範與各模組實作細節。

---

## 漸進式閱讀規範 (Progressive Reading Rule)

> [!IMPORTANT]
> **嚴禁一次載入或閱讀所有文檔**！
> 所有 Agent（實作與驗證）在執行任務前，必須先參閱本目錄，並依循以下分流規則**僅讀取當前任務所需之特定檔案**：

```text
                  [ docs/index.md (本索引入口) ]
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
   【掌握整體進度與當前狀態】                 【深入單一模組實作/驗證】
       docs/progress.md                      docs/modules/
                                                  ├── 01_database.md
                                                  ├── 02_crawlers.md
                                                  ├── 03_analysis.md
                                                  └── 04_app.md
```

---

## 模組文檔索引 (按需讀取)

| 文檔路徑 | 負責範疇 | 何時讀取此檔案 |
| :--- | :--- | :--- |
| [docs/progress.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/progress.md) | 全域里程碑、各階段開發進度、驗證狀態 (PASS/FAIL) | 任務開始前確認當前進度與相依模組狀態 |
| [docs/modules/01_database.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/modules/01_database.md) | SQLite + SQLAlchemy 表結構、欄位定義、關聯約束、CRUD 規格 | 開發或驗證 `database/` 資料表與管理模組時 |
| [docs/modules/02_crawlers.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/modules/02_crawlers.md) | 原價屋、PTT、FB 社團爬蟲目標、Regex 清洗規則與型號群組映射 | 開發或驗證 `crawlers/` 擷取與解析模組時 |
| [docs/modules/03_analysis.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/modules/03_analysis.md) | 四大分析演算法公式（歷史百分位、二手折數、跨平台比價、異常低價評估） | 開發或驗證 `analysis/` 數據運算邏輯時 |
| [docs/modules/04_app.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/modules/04_app.md) | Streamlit 前端佈局、Plotly 圖表呈現、篩選列與表格狀態 | 開發或驗證 `app/` 介面渲染與整合時 |
| [docs/modules/05_llm_parser.md](file:///c:/Users/berto/Desktop/my/learning/web%20crawler/midterm_project/docs/modules/05_llm_parser.md) | OpenRouter 免費模型 API 調用、結構化 Prompt 與貼文解析規格 | 開發或驗證 LLM 貼文清洗模組時 |

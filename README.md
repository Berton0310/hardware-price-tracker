# 電腦硬體新品歷史價格與二手市場行情比價分析系統

本專案為期中專題實作專用目錄，後續所有爬蟲模組、資料庫設計、資料分析與視覺化應用皆在此處開發與管理。

## 預計專案結構規劃

```text
midterm_project/
├── crawlers/             # 網路爬蟲模組
│   ├── coolpc_crawler.py # 原價屋新品估價爬蟲 (requests + BeautifulSoup4)
│   ├── ptt_crawler.py    # PTT HardwareSale 二手版爬蟲 (requests + BeautifulSoup4)
│   └── fb_crawler.py     # Facebook 二手社團爬蟲 (Playwright / Selenium)
├── database/             # 資料庫與模型定義
│   ├── models.py         # SQLAlchemy ORM 資料表模型 (products, new_price_history, used_market_listings)
│   └── db_manager.py     # 資料庫連線、初始化與 CRUD 模組
├── data/                 # 資料庫檔案與暫存資料
│   └── hardware.db       # SQLite 資料庫檔案
├── analysis/             # 資料分析與統計模組
│   └── price_analysis.py # 價格位階、真假降價、二手折數與甜價評估計算
├── app/                  # 應用呈現與介面
│   └── main_app.py       # Streamlit 互動式比價儀表板
├── requirements.txt      # 專案相依套件清單
└── README.md             # 專案說明文件
```

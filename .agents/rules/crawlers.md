---
trigger: glob
globs: "crawlers/**, cleaners/**, login_fb.py, scripts/crawl_fb.py"
---

# 爬蟲與清洗層規則

- 原價屋頁面：`resp.content.decode("cp950", errors="replace")`，不要用 `big5`。
- 價格 regex：雙價格與單價格分開處理，不要在同一個 regex 裡放兩個可匹配數字的群組（ERR-002）。
- 二手貼文：排除假標價（如 `$99999`）並設異常高低價黑名單，優先取「實售」「售價」之類修飾語後面的數字（ERR-003）。
- 保固狀態要區分「原廠保內／過保／終保」，「終保」與「終身保固」都要辨識（ERR-004、ERR-008）。
- `spec_group`、`category` 等群組欄位：各爬蟲與清洗層用同一套命名（ERR-009）。改命名時，一併檢查 `database/` 與 `app/` 的用法。
- GPU、CPU、主機板新品：設最低價底線與配件關鍵字黑名單，避免轉接線等配件拉低新品最低價（ERR-014）。
- LLM fallback 解析：先清掉方括號標籤（如 `[賣`）與平台名稱（如 `Facebook`），再當作規格標籤；OpenRouter 模型失效（404）時要能退回 fallback 解析器（ERR-013），而且任何降級都要記 warning，不能靜默（ERR-020）。
- `used_asking_price` 的 `0` 代表「未開價」（欄位 NOT NULL）。新抓到的刊登解析不到價格時存 `0`，分析層會剔除；重新清洗既有資料時，解析不到就保留原本的價格，不得覆蓋成 `0`，並記 warning（ERR-015、ERR-019）。
- 網路請求只捕捉 `requests.exceptions.RequestException`（Playwright 用它的 `Error`／`TimeoutError`），而且要記 warning 再跳過；不要用 `except Exception` 吞掉解析或程式錯誤（ERR-019）。整次爬取最外層的邊界可以捕捉 `Exception`，但必須記 log。
- 請求一定設 `timeout`；不要調低請求間隔。

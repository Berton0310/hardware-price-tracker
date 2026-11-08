# 模組規格：OpenRouter LLM 非結構化貼文清洗 (docs/modules/05_llm_parser.md)

本文件定義透過 OpenRouter 免費模型解析二手貼文自然語言之實作規格。

---

## 1. 核心技術選型
* **API 提供商**：OpenRouter (`https://openrouter.ai/api/v1`)
* **預設模型**：OpenRouter 支援之免費模型（如 `google/gemini-2.0-flash-exp:free`、`meta-llama/llama-3.3-70b-instruct:free`、`deepseek/deepseek-chat:free`）
* **憑證管理**：由專案根目錄 `.env` 讀取 `OPENROUTER_API_KEY`（已加入 `.gitignore`）

---

## 2. 結構化 Prompt 與輸出 Schema
輸入貼文標題與全文，LLM 必須回傳嚴格之 JSON 格式：

```json
{
  "category": "GPU",
  "spec_group": "RTX 4060 Ti 16G",
  "brand": "ASUS",
  "model_name": "DUAL-RTX4060TI-O16G",
  "used_asking_price": 10500,
  "warranty_status": "原廠保內",
  "seller_type": "個人自售",
  "trade_method": "台北面交 / 賣貨便",
  "condition_notes": "盒裝完整"
}
```

---

## 3. 容錯與省錢機制 (刪到不能再刪)
1. **快取防重複**：以 `post_id` 為防線，若已在資料庫中存在且未更動，絕不重複向 OpenRouter 發送請求。
2. **失敗降級 (Fallback)**：若 OpenRouter 免費模型遇到 Rate Limit 或連線超時，自動暫存原始文字並標註 `status='pending_llm'`，不卡死主爬蟲流程。

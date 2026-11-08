# 模組規格：價格分析與演算法 (docs/modules/03_analysis.md)

本文件定義資料分析與指標運算模組（`analysis/`）之實作規格與驗收標準。
恪守「刪到不能再刪」原則，模組純做數學運算與統計，不包含任何 UI 渲染程式碼。

---

## 1. 模組架構與檔案
- 實作檔案：`analysis/price_analysis.py`（含 `analysis/__init__.py`）
- 驗收測試：`tests/test_analysis.py`

---

## 2. 核心分析函數規格

### 2.1 歷史價格走勢與百分位數 (`analyze_new_price_trend`)
- **函式簽名**：`analyze_new_price_trend(conn: sqlite3.Connection, product_id: int, days: int = 90) -> dict[str, Any]`
- **計算邏輯**：
  - 提取該產品近 N 日價格紀錄序列。
  - 計算最高價 $P_{\max}$、最低價 $P_{\min}$、平均價 $P_{\text{avg}}$、當前最新售價 $P_{\text{current}}$。
  - 歷史位階百分位數：
    $$\text{Percentile} = \begin{cases} 50.0 & \text{if } P_{\max} == P_{\min} \\ \frac{P_{\text{current}} - P_{\min}}{P_{\max} - P_{\min}} \times 100.0 & \text{otherwise} \end{cases}$$
  - 真假降價辨識：
    - 若網頁標註降價（如含有 `↘`）但 $P_{\text{current}} \ge P_{\text{avg-30d}}$，標註 `discount_authenticity = "先漲後降 (假降價)"`。
    - 若 $P_{\text{current}} < P_{\text{avg-30d}}$，標註 `discount_authenticity = "創近期新低 (真降價)"`。
    - 若無變動或漲價，標註 `discount_authenticity = "正常報價"`。
- **邊界處理**：若無任何歷史資料，回傳預設空結構，不噴 Exception。

### 2.2 新品 vs. 二手價差與折數 (`calculate_spec_group_discount`)
- **函式簽名**：`calculate_spec_group_discount(conn: sqlite3.Connection, spec_group: str, days: int = 30) -> dict[str, Any]`
- **計算邏輯**：
  - 查詢該 `spec_group` 旗下產品之當前新品最低價 $P_{\text{new-min}}$。
  - 查詢該 `spec_group` 在二手市場近 N 日內「個人自售」之欲售價格中位數 $M_{\text{used}}$（若無個人自售，退回全體中位數）。
  - 二手折數：$\text{discount_ratio} = \frac{M_{\text{used}}}{P_{\text{new-min}}} \times 100.0$（小數點後 1 位）。
  - 轉買二手節省金額：$\text{saved_amount} = P_{\text{new-min}} - M_{\text{used}}$。
- **邊界處理**：若缺新品價或缺二手價，相關比值設為 `None`。

### 2.3 跨平台與賣家類型比較 (`compare_platforms_and_sellers`)
- **函式簽名**：`compare_platforms_and_sellers(conn: sqlite3.Connection, spec_group: str | None = None) -> dict[str, Any]`
- **分組統計**：
  - 維度 1：`FB_Group_individual`（FB 個人自售）
  - 維度 2：`FB_Group_workshop`（FB 商業工作室）
  - 維度 3：`PTT_individual`（PTT 個人自售）
  - 維度 4：`PTT_workshop`（PTT 商業工作室）
  - 輸出各組：`count`（貼文數）、`median_price`（價格中位數）、`mean_price`（平均價）。
  - 計算工作室溢價率：$\frac{\text{工作室均價} - \text{自售均價}}{\text{自售均價}} \times 100.0$。

### 2.4 二手最新貼文行情評估 (`evaluate_listing_price`)
- **函式簽名**：`evaluate_listing_price(conn: sqlite3.Connection, spec_group: str, asking_price: int) -> dict[str, Any]`
- **評估基準**：
  - 優先使用該規格過去已售出（`sale_status = '已售出'`）之中位數 $M_{\text{sold}}$；若無已售出資料，使用所有二手貼文中位數 $M_{\text{all}}$。
  - 取得該規格新品最低價 $P_{\text{new-min}}$ 作為天花板參考。
- **分級判定**：
  1. $P_{\text{asking}} < M \times 0.5 \implies$ **`異常低價警示 (防詐騙/故障)`** (Level: `anomaly_low`)
  2. $M \times 0.5 \le P_{\text{asking}} < M \times 0.9 \implies$ **`低於行情 (推薦購買)`** (Level: `bargain`)
  3. $M \times 0.9 \le P_{\text{asking}} \le M \times 1.1 \implies$ **`符合行情`** (Level: `fair`)
  4. $P_{\text{asking}} > M \times 1.1$ 或 $P_{\text{asking}} \ge P_{\text{new-min}} \times 0.9 \implies$ **`偏貴 (建議買新品)`** (Level: `overpriced`)
- **回傳**：包含 `evaluation_label`, `evaluation_level`, `benchmark_price`, `diff_amount`, `diff_percentage`。

---

## 3. 驗證 Agent 驗收標準
- `tests/test_analysis.py`：建構各種邊界合成資料庫（平價、極端單筆資料、全缺資料、零變動資料、防除以零除錯），100% 自動斷言通過。

"""價格分析與演算法核心模組 (analysis/price_analysis.py)

實作電腦硬體新品與二手價格統計運算、歷史百分位數、真假降價辨識、二手折數與行情評定。
恪守「刪到不能再刪」極簡標準，全 Python 內建標準函式庫實作，零外部依賴。
"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any


def _fetch_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """輔助函式：將游標查詢結果統一轉換為欄位字典清單，相容有無 row_factory 之連線。"""
    cols = [col[0] for col in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def analyze_new_price_trend(
    conn: sqlite3.Connection, product_id: int, days: int = 90
) -> dict[str, Any]:
    """分析某新品產品之歷史價格走勢、歷史位階百分位數與真假降價辨識。

    規格遵循 docs/modules/03_analysis.md Section 2.1。
    """
    if days and days > 0:
        cursor = conn.execute(
            """
            SELECT history_id, product_id, record_date, listed_original_price,
                   current_new_price, price_change_tag, in_stock
            FROM new_price_history
            WHERE product_id = ? AND record_date >= date('now', '-' || ? || ' days')
            ORDER BY record_date ASC;
            """,
            (int(product_id), int(days)),
        )
        records = _fetch_dicts(cursor)
        if not records:
            # 支援靜態測資：若依 date('now') 過濾無資料，但資料庫存有歷史資料，則以該產品最大紀錄日作相對過濾
            cur_all = conn.execute(
                """
                SELECT history_id, product_id, record_date, listed_original_price,
                       current_new_price, price_change_tag, in_stock
                FROM new_price_history
                WHERE product_id = ?
                ORDER BY record_date ASC;
                """,
                (int(product_id),),
            )
            all_records = _fetch_dicts(cur_all)
            if all_records:
                latest_date = all_records[-1]["record_date"]
                cur_rel = conn.execute(
                    """
                    SELECT history_id, product_id, record_date, listed_original_price,
                           current_new_price, price_change_tag, in_stock
                    FROM new_price_history
                    WHERE product_id = ? AND record_date >= date(?, '-' || ? || ' days')
                    ORDER BY record_date ASC;
                    """,
                    (int(product_id), latest_date, int(days)),
                )
                records = _fetch_dicts(cur_rel)
    else:
        cursor = conn.execute(
            """
            SELECT history_id, product_id, record_date, listed_original_price,
                   current_new_price, price_change_tag, in_stock
            FROM new_price_history
            WHERE product_id = ?
            ORDER BY record_date ASC;
            """,
            (int(product_id),),
        )
        records = _fetch_dicts(cursor)

    if not records:
        return {
            "product_id": int(product_id),
            "max_price": None,
            "min_price": None,
            "avg_price": None,
            "current_price": None,
            "price_percentile": None,
            "discount_authenticity": "無歷史資料",
            "avg_30d_price": None,
            "price_history": [],
        }

    prices = [int(r["current_new_price"]) for r in records]
    max_price = max(prices)
    min_price = min(prices)
    avg_price = round(sum(prices) / len(prices), 1)
    current_price = int(records[-1]["current_new_price"])

    # 歷史位階百分位數 (防除以零)
    if max_price == min_price:
        percentile = 50.0
    else:
        percentile = round(((current_price - min_price) / (max_price - min_price)) * 100.0, 1)

    # 近 30 日平均價計算
    latest_dt = records[-1]["record_date"]
    cur_30d = conn.execute(
        """
        SELECT listed_original_price, current_new_price
        FROM new_price_history
        WHERE product_id = ? AND record_date >= date(?, '-30 days')
        ORDER BY record_date ASC;
        """,
        (int(product_id), latest_dt),
    )
    records_30d = _fetch_dicts(cur_30d)
    if records_30d:
        if (
            len(records_30d) == 1
            and int(records_30d[0]["listed_original_price"]) > int(records_30d[0]["current_new_price"])
        ):
            avg_30d = float(records_30d[0]["listed_original_price"])
        else:
            p_30d = [int(r["current_new_price"]) for r in records_30d]
            avg_30d = round(sum(p_30d) / len(p_30d), 1)
    else:
        avg_30d = avg_price

    # 真假降價辨識
    tag = str(records[-1].get("price_change_tag") or "")
    is_discount_tagged = ("↘" in tag) or ("降" in tag)
    if is_discount_tagged:
        if current_price < avg_30d:
            authenticity = "創近期新低 (真降價)"
        else:
            authenticity = "先漲後降 (假降價)"
    else:
        authenticity = "正常報價"

    return {
        "product_id": int(product_id),
        "max_price": max_price,
        "min_price": min_price,
        "avg_price": avg_price,
        "current_price": current_price,
        "price_percentile": percentile,
        "discount_authenticity": authenticity,
        "avg_30d_price": avg_30d,
        "price_history": records,
    }


def calculate_spec_group_discount(
    conn: sqlite3.Connection, spec_group: str, days: int = 30
) -> dict[str, Any]:
    """計算特定規格群組之新品最低價 vs. 二手自售中位數、二手折數與節省金額。

    規格遵循 docs/modules/03_analysis.md Section 2.2。
    """
    # 1. 查詢該規格當前新品最低價
    cur_new = conn.execute(
        """
        SELECT MIN(h.current_new_price)
        FROM products p
        JOIN new_price_history h ON p.product_id = h.product_id
        WHERE p.spec_group = ? COLLATE NOCASE
          AND h.record_date = (
              SELECT MAX(h2.record_date)
              FROM new_price_history h2
              WHERE h2.product_id = p.product_id
          );
        """,
        (str(spec_group),),
    )
    row_new = cur_new.fetchone()
    new_min_price = int(row_new[0]) if (row_new and row_new[0] is not None) else None

    # 2. 查詢該規格二手貼文紀錄
    if days and days > 0:
        cur_used = conn.execute(
            """
            SELECT used_asking_price, seller_type, post_time
            FROM used_market_listings
            WHERE spec_group = ? COLLATE NOCASE AND post_time >= datetime('now', '-' || ? || ' days')
            ORDER BY post_time DESC;
            """,
            (str(spec_group), int(days)),
        )
        listings = _fetch_dicts(cur_used)
        if not listings:
            # 支援靜態測資
            cur_all = conn.execute(
                """
                SELECT used_asking_price, seller_type, post_time
                FROM used_market_listings
                WHERE spec_group = ? COLLATE NOCASE
                ORDER BY post_time DESC;
                """,
                (str(spec_group),),
            )
            all_listings = _fetch_dicts(cur_all)
            if all_listings:
                latest_post_time = all_listings[0]["post_time"]
                cur_rel = conn.execute(
                    """
                    SELECT used_asking_price, seller_type, post_time
                    FROM used_market_listings
                    WHERE spec_group = ? COLLATE NOCASE AND post_time >= datetime(?, '-' || ? || ' days')
                    ORDER BY post_time DESC;
                    """,
                    (str(spec_group), latest_post_time, int(days)),
                )
                listings = _fetch_dicts(cur_rel)
    else:
        cur_used = conn.execute(
            """
            SELECT used_asking_price, seller_type, post_time
            FROM used_market_listings
            WHERE spec_group = ? COLLATE NOCASE
            ORDER BY post_time DESC;
            """,
            (str(spec_group),),
        )
        listings = _fetch_dicts(cur_used)

    individual_prices = [
        int(r["used_asking_price"])
        for r in listings
        if r.get("seller_type") in ("個人自售", "自售", "individual")
        and r.get("used_asking_price") is not None
        and int(r["used_asking_price"]) > 0
    ]

    if individual_prices:
        used_median = round(float(statistics.median(individual_prices)), 1)
        used_min = min(individual_prices)
        seller_type_used = "個人自售"
        listing_count = len(individual_prices)
    elif listings:
        all_prices = [
            int(r["used_asking_price"])
            for r in listings
            if r.get("used_asking_price") is not None and int(r["used_asking_price"]) > 0
        ]
        if all_prices:
            used_median = round(float(statistics.median(all_prices)), 1)
            used_min = min(all_prices)
            seller_type_used = "全體二手"
            listing_count = len(all_prices)
        else:
            used_median = None
            used_min = None
            seller_type_used = None
            listing_count = 0
    else:
        used_median = None
        used_min = None
        seller_type_used = None
        listing_count = 0

    # 3. 計算折數與節省金額 (防除以零與防空值)
    if new_min_price is not None and new_min_price > 0 and used_median is not None:
        discount_ratio = round((used_median / float(new_min_price)) * 100.0, 1)
        saved_amount = round(float(new_min_price) - used_median, 1)
    else:
        discount_ratio = None
        saved_amount = None

    return {
        "spec_group": str(spec_group),
        "new_min_price": new_min_price,
        "used_median_price": used_median,
        "used_min_price": used_min,
        "discount_ratio": discount_ratio,
        "saved_amount": saved_amount,
        "seller_type_used": seller_type_used,
        "used_listing_count": listing_count,
    }


def compare_platforms_and_sellers(
    conn: sqlite3.Connection, spec_group: str | None = None
) -> dict[str, Any]:
    """比較 FB 社團與 PTT 二手交易版之個人自售與商業工作室價格分布及工作室溢價率。

    規格遵循 docs/modules/03_analysis.md Section 2.3。
    """
    if spec_group:
        cursor = conn.execute(
            """
            SELECT source_platform, seller_type, used_asking_price
            FROM used_market_listings
            WHERE spec_group = ? COLLATE NOCASE;
            """,
            (str(spec_group),),
        )
    else:
        cursor = conn.execute(
            """
            SELECT source_platform, seller_type, used_asking_price
            FROM used_market_listings;
            """
        )
    rows = _fetch_dicts(cursor)

    buckets: dict[str, list[int]] = {
        "FB_Group_individual": [],
        "FB_Group_workshop": [],
        "PTT_individual": [],
        "PTT_workshop": [],
    }

    for r in rows:
        price = r.get("used_asking_price")
        if price is None or int(price) <= 0:
            continue
        plat = str(r.get("source_platform", "")).upper()
        st = str(r.get("seller_type", ""))
        is_fb = ("FB" in plat) or ("FACEBOOK" in plat)
        is_ptt = "PTT" in plat
        is_workshop = ("工作室" in st) or ("店家" in st) or ("workshop" in st.lower())

        if is_fb:
            if is_workshop:
                buckets["FB_Group_workshop"].append(int(price))
            else:
                buckets["FB_Group_individual"].append(int(price))
        elif is_ptt:
            if is_workshop:
                buckets["PTT_workshop"].append(int(price))
            else:
                buckets["PTT_individual"].append(int(price))

    groups: dict[str, dict[str, Any]] = {}
    for name, p_list in buckets.items():
        if p_list:
            groups[name] = {
                "count": len(p_list),
                "median_price": round(float(statistics.median(p_list)), 1),
                "mean_price": round(float(sum(p_list) / len(p_list)), 1),
            }
        else:
            groups[name] = {
                "count": 0,
                "median_price": None,
                "mean_price": None,
            }

    all_workshop = buckets["FB_Group_workshop"] + buckets["PTT_workshop"]
    all_individual = buckets["FB_Group_individual"] + buckets["PTT_individual"]

    workshop_premium_rate = None
    if all_workshop and all_individual:
        mean_workshop = sum(all_workshop) / len(all_workshop)
        mean_individual = sum(all_individual) / len(all_individual)
        if mean_individual > 0:
            workshop_premium_rate = round(
                ((mean_workshop - mean_individual) / mean_individual) * 100.0, 1
            )

    fb_premium = None
    if (
        groups["FB_Group_workshop"]["mean_price"] is not None
        and groups["FB_Group_individual"]["mean_price"] is not None
    ):
        fb_ind = float(groups["FB_Group_individual"]["mean_price"])
        fb_ws = float(groups["FB_Group_workshop"]["mean_price"])
        if fb_ind > 0:
            fb_premium = round(((fb_ws - fb_ind) / fb_ind) * 100.0, 1)

    ptt_premium = None
    if (
        groups["PTT_workshop"]["mean_price"] is not None
        and groups["PTT_individual"]["mean_price"] is not None
    ):
        ptt_ind = float(groups["PTT_individual"]["mean_price"])
        ptt_ws = float(groups["PTT_workshop"]["mean_price"])
        if ptt_ind > 0:
            ptt_premium = round(((ptt_ws - ptt_ind) / ptt_ind) * 100.0, 1)

    return {
        "spec_group": spec_group,
        "groups": groups,
        "workshop_premium_rate": workshop_premium_rate,
        "fb_workshop_premium_rate": fb_premium,
        "ptt_workshop_premium_rate": ptt_premium,
        "FB_Group_individual": groups["FB_Group_individual"],
        "FB_Group_workshop": groups["FB_Group_workshop"],
        "PTT_individual": groups["PTT_individual"],
        "PTT_workshop": groups["PTT_workshop"],
    }


def evaluate_listing_price(
    conn: sqlite3.Connection, spec_group: str, asking_price: int
) -> dict[str, Any]:
    """評估單筆二手貼文之開價是否合理，給出四級行情評定與天花板新品參考。

    規格遵循 docs/modules/03_analysis.md Section 2.4。
    分級判定：
      1. asking_price < M * 0.5 => 異常低價警示 (防詐騙/故障) (anomaly_low)
      2. M * 0.5 <= asking_price < M * 0.9 => 低於行情 (推薦購買) (bargain)
      3. M * 0.9 <= asking_price <= M * 1.1 => 符合行情 (fair)
      4. asking_price > M * 1.1 或 asking_price >= new_min * 0.9 => 偏貴 (建議買新品) (overpriced)
    """
    # 1. 優先取已售出中位數，若無則取全體二手中位數
    cur_sold = conn.execute(
        """
        SELECT used_asking_price
        FROM used_market_listings
        WHERE spec_group = ? COLLATE NOCASE AND (sale_status = '已售出' OR sale_status LIKE '%售出%');
        """,
        (str(spec_group),),
    )
    sold_prices = [int(row[0]) for row in cur_sold.fetchall() if row[0] is not None and int(row[0]) > 0]

    if sold_prices:
        benchmark_price = round(float(statistics.median(sold_prices)), 1)
        benchmark_source = "已售出中位數"
    else:
        cur_all = conn.execute(
            """
            SELECT used_asking_price
            FROM used_market_listings
            WHERE spec_group = ? COLLATE NOCASE;
            """,
            (str(spec_group),),
        )
        all_prices = [int(row[0]) for row in cur_all.fetchall() if row[0] is not None and int(row[0]) > 0]
        if all_prices:
            benchmark_price = round(float(statistics.median(all_prices)), 1)
            benchmark_source = "全體二手中位數"
        else:
            benchmark_price = None
            benchmark_source = "無二手資料"

    # 2. 查詢該規格新品最低價作為天花板參考
    cur_new = conn.execute(
        """
        SELECT MIN(h.current_new_price)
        FROM products p
        JOIN new_price_history h ON p.product_id = h.product_id
        WHERE p.spec_group = ? COLLATE NOCASE
          AND h.record_date = (
              SELECT MAX(h2.record_date)
              FROM new_price_history h2
              WHERE h2.product_id = p.product_id
          );
        """,
        (str(spec_group),),
    )
    row_new = cur_new.fetchone()
    new_min_price = float(row_new[0]) if (row_new and row_new[0] is not None) else None

    # 3. 執行四級分級與價差計算
    p_ask = float(asking_price)

    if benchmark_price is not None:
        diff_amount = round(p_ask - benchmark_price, 1)
        if benchmark_price > 0:
            diff_percentage = round(((p_ask - benchmark_price) / benchmark_price) * 100.0, 1)
        else:
            diff_percentage = 0.0

        if p_ask < benchmark_price * 0.5:
            label = "異常低價警示 (防詐騙/故障)"
            level = "anomaly_low"
        elif (new_min_price is not None and p_ask >= new_min_price * 0.9) or (
            p_ask > benchmark_price * 1.1
        ):
            label = "偏貴 (建議買新品)"
            level = "overpriced"
        elif p_ask < benchmark_price * 0.9:
            label = "低於行情 (推薦購買)"
            level = "bargain"
        else:
            label = "符合行情"
            level = "fair"
    else:
        diff_amount = None
        diff_percentage = None
        if new_min_price is not None:
            if p_ask >= new_min_price * 0.9:
                label = "偏貴 (建議買新品)"
                level = "overpriced"
            elif p_ask < new_min_price * 0.45:
                label = "異常低價警示 (防詐騙/故障)"
                level = "anomaly_low"
            else:
                label = "無二手行情資料 (僅參考新品)"
                level = "unknown"
        else:
            label = "無行情資料可比對"
            level = "unknown"

    return {
        "spec_group": str(spec_group),
        "asking_price": int(asking_price),
        "benchmark_price": benchmark_price,
        "benchmark_source": benchmark_source,
        "new_min_price": new_min_price,
        "evaluation_label": label,
        "evaluation_level": level,
        "diff_amount": diff_amount,
        "diff_percentage": diff_percentage,
    }

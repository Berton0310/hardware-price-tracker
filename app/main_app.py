"""電腦硬體新品與二手市場比價分析系統 Streamlit 儀表板 (app/main_app.py)

遵循 CLAUDE.md、AGENTS.md 與 docs/modules/04_app.md 規範。
恪守「刪到不能再刪」標準，函式權責分離（資料查詢、圖表建立、頁面渲染），具備全方位空資料防護。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

# 確保專案根目錄納入模組搜尋路徑 (解決直接執行 streamlit run app/main_app.py 時的 ModuleNotFoundError)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitAPIException

from analysis.price_analysis import (
    analyze_new_price_trend,
    calculate_spec_group_discount,
    compare_platforms_and_sellers,
    evaluate_listing_price,
)
from cleaners.llm_parser import _extract_category
from database.db_manager import get_latest_sync_log, init_db
from services.sync_service import execute_sync

logger = logging.getLogger(__name__)

# 核心常數設定
CATEGORIES: list[str] = ["GPU", "RAM", "SSD", "CPU"]

DEFAULT_SPEC_GROUPS: dict[str, list[str]] = {
    "GPU": ["RTX 4060 Ti 16G", "RTX 4070 SUPER", "RTX 4060 8G", "RX 7800 XT", "RTX 3060 12G"],
    "RAM": ["DDR5-6000 32G", "DDR4-3200 16G", "DDR5-5600 32G", "DDR4-3600 32G"],
    "SSD": ["1TB PCIe 4.0 SSD", "2TB PCIe 4.0 SSD", "500GB PCIe 4.0 SSD", "4TB PCIe 4.0 SSD"],
    "CPU": ["i7-14700K", "i5-14600K", "Ryzen 7 7800X3D", "Ryzen 5 7600", "i9-14900K"],
}


# ==============================================================================
# 1. 資料庫連線與查詢邏輯 (Data Access & Query Logic)
# ==============================================================================


def get_db(db_path: str = "data/hardware.db") -> sqlite3.Connection:
    """獲取 SQLite 資料庫連線並確保 Schema 初始化。支援環境變數 HARDWARE_DB_PATH 覆寫。"""
    actual_path = os.environ.get("HARDWARE_DB_PATH", db_path)
    return init_db(actual_path)


def get_spec_groups(conn: sqlite3.Connection, category: str) -> list[str]:
    """依類別動態查詢資料庫內所有規格群組（整合新品產品主檔與二手市場刊登）；若無資料則回傳預設列表。"""
    try:
        # 1. 查詢新品產品中符合該類別的規格群組
        cur_prod = conn.execute(
            """
            SELECT DISTINCT spec_group
            FROM products
            WHERE category = ? AND spec_group IS NOT NULL AND TRIM(spec_group) != ''
            """,
            (category,),
        )
        prod_groups = {str(r[0]).strip() for r in cur_prod.fetchall() if r[0] and str(r[0]).strip()}

        # 2. 查詢二手貼文中符合該類別的規格群組 (關聯 products 或透過分類特徵比對，避免原價屋新品停售導致二手活躍規格遺漏)
        cur_used = conn.execute(
            """
            SELECT DISTINCT u.spec_group, p.category
            FROM used_market_listings u
            LEFT JOIN products p ON u.product_id = p.product_id
            WHERE u.spec_group IS NOT NULL AND TRIM(u.spec_group) != ''
            """
        )
        used_groups = set()
        for r in cur_used.fetchall():
            s_grp = str(r[0]).strip()
            p_cat = str(r[1]).strip() if r[1] else ""
            if p_cat == category or _extract_category(s_grp) == category:
                if not s_grp.startswith("[") and s_grp.lower() not in ("facebook", "unknown", "other"):
                    used_groups.add(s_grp)

        all_groups = prod_groups.union(used_groups)
        if not all_groups:
            return DEFAULT_SPEC_GROUPS.get(category, ["RTX 4060 Ti 16G"])
        return sorted(list(all_groups))
    except Exception:
        logger.exception("載入規格群組失敗，改用預設清單")
        return DEFAULT_SPEC_GROUPS.get(category, ["RTX 4060 Ti 16G"])


def get_representative_product(
    conn: sqlite3.Connection, spec_group: str
) -> dict[str, Any] | None:
    """查詢該規格群組中最新報價最低的代表產品（若無價格則取首筆產品主檔）。"""
    try:
        cur = conn.execute(
            """
            SELECT p.product_id, p.model_name, p.brand, h.current_new_price
            FROM products p
            JOIN new_price_history h ON p.product_id = h.product_id
            WHERE p.spec_group = ?
              AND h.record_date = (
                  SELECT MAX(h2.record_date)
                  FROM new_price_history h2
                  WHERE h2.product_id = p.product_id
              )
            ORDER BY h.current_new_price ASC
            LIMIT 1;
            """,
            (spec_group,),
        )
        row = cur.fetchone()
        if row:
            return {
                "product_id": int(row[0]),
                "model_name": str(row[1]),
                "brand": str(row[2]),
                "current_new_price": int(row[3]),
            }

        cur2 = conn.execute(
            "SELECT product_id, model_name, brand FROM products WHERE spec_group = ? LIMIT 1;",
            (spec_group,),
        )
        row2 = cur2.fetchone()
        if row2:
            return {
                "product_id": int(row2[0]),
                "model_name": str(row2[1]),
                "brand": str(row2[2]),
                "current_new_price": None,
            }
        return None
    except Exception:
        logger.exception("載入新品價格資訊失敗")
        return None


def get_filtered_listings(
    conn: sqlite3.Connection,
    spec_group: str,
    exclude_workshops: bool = True,
    only_in_warranty: bool = False,
) -> list[dict[str, Any]]:
    """依側邊欄開關過濾二手貼文（排除商業工作室、僅看原廠保內），依發文時間降冪排列。"""
    try:
        cur = conn.execute(
            """
            SELECT u.post_id, u.product_id, u.spec_group, u.source_platform, u.seller_name,
                   u.seller_type, u.used_asking_price, u.warranty_status, u.trade_method,
                   u.comment_count, u.sale_status, u.post_time, u.post_url,
                   p.model_name
            FROM used_market_listings u
            LEFT JOIN products p ON u.product_id = p.product_id
            WHERE u.spec_group = ?
            ORDER BY u.post_time DESC;
            """,
            (spec_group,),
        )
        cols = [col[0] for col in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        filtered = []
        for r in rows:
            st_type = str(r.get("seller_type") or "")
            is_workshop = ("工作室" in st_type) or ("店家" in st_type) or ("workshop" in st_type.lower())
            if exclude_workshops and is_workshop:
                continue

            warranty = str(r.get("warranty_status") or "")
            if only_in_warranty:
                is_in_warranty = (
                    "保內" in warranty
                    or "終保" in warranty
                    or "終身" in warranty
                    or "原廠保" in warranty
                    or "保固中" in warranty
                    or warranty in ("保內", "終保", "有保")
                )
                if not is_in_warranty:
                    continue

            filtered.append(r)
        return filtered
    except Exception:
        logger.exception("載入二手刊登資料失敗")
        return []


# ==============================================================================
# 2. 圖表與資料格式化 (Visualizations & Data Preparation)
# ==============================================================================


def format_metric_card_data(
    trend_info: dict[str, Any], discount_info: dict[str, Any]
) -> dict[str, Any]:
    """格式化頂部 4 大重點指標卡所需的展示文字與說明。"""
    new_min = discount_info.get("new_min_price")
    has_valid_new_price = new_min is not None and new_min > 0
    has_trend_price = trend_info.get("current_price") is not None and trend_info["current_price"] > 0
    used_med = discount_info.get("used_median_price")
    has_used_price = used_med is not None and used_med > 0

    # 1. 今日新品最低價
    if has_valid_new_price:
        card1_val = f"${int(new_min):,}"
        pct = trend_info.get("price_percentile")
        auth = trend_info.get("discount_authenticity", "無歷史資料")
        if pct is not None:
            if pct <= 25:
                pos = "低檔"
            elif pct >= 75:
                pos = "高檔"
            else:
                pos = "中檔"
            card1_delta = f"第 {int(round(pct))} 百分位 ({pos}) · {auth}"
        else:
            card1_delta = auth if auth != "無歷史資料" else "尚無走勢紀錄"
    elif has_trend_price:
        card1_val = f"${int(trend_info['current_price']):,}"
        card1_delta = trend_info.get("discount_authenticity", "正常報價")
    elif has_used_price:
        # 當二手型號在原價屋已停售（new_min_price 為 None/0）時
        card1_val = "原價屋目前無新品在售"
        card1_delta = "已停售或為二手特規"
    else:
        card1_val = "無新品報價"
        card1_delta = "尚無走勢紀錄"

    # 2. 二手市場中位價 (呈現二手流通中位數、最低價與有效貼文數)
    used_min = discount_info.get("used_min_price")
    count = discount_info.get("used_listing_count", 0)
    seller_type = discount_info.get("seller_type_used") or "二手市場"
    if has_used_price:
        card2_val = f"${int(round(used_med)):,}"
        min_prefix = (
            f"最低 ${int(round(used_min)):,} · "
            if (used_min is not None and used_min > 0 and not has_valid_new_price)
            else ""
        )
        card2_delta = f"{min_prefix}{seller_type} ({count} 筆)"
    else:
        card2_val = "無二手數據"
        card2_delta = "暫無市場刊登"

    # 3. 二手折數
    ratio = discount_info.get("discount_ratio")
    if ratio is not None:
        card3_val = f"{ratio:.1f} 折"
        card3_delta = f"為新品價之 {ratio:.1f}%"
    elif has_used_price and not has_valid_new_price:
        card3_val = "無折數數據"
        card3_delta = "原價屋無新品，無法比價"
    else:
        card3_val = "無折數數據"
        card3_delta = "缺少新品或二手價"

    # 4. 轉買二手可省金額
    saved = discount_info.get("saved_amount")
    if saved is not None:
        if saved >= 0:
            card4_val = f"節省 ${int(round(saved)):,} 元"
            card4_delta = "比買新品划算"
        else:
            card4_val = f"貴 ${int(round(abs(saved))):,} 元"
            card4_delta = "二手反比新品偏貴"
    elif has_used_price and not has_valid_new_price:
        card4_val = "無價差數據"
        card4_delta = "原價屋已停售無新品價"
    else:
        card4_val = "無數據"
        card4_delta = "缺少比價基準"

    return {
        "card1_val": card1_val,
        "card1_delta": card1_delta,
        "card2_val": card2_val,
        "card2_delta": card2_delta,
        "card3_val": card3_val,
        "card3_delta": card3_delta,
        "card4_val": card4_val,
        "card4_delta": card4_delta,
    }


def create_price_trend_chart(trend_info: dict[str, Any], spec_group: str) -> go.Figure:
    """繪製新品 90 天價格走勢折線圖，標示最高、最低與均價參考線及真假降價警語。"""
    fig = go.Figure()
    history = trend_info.get("price_history") or []
    valid_history = [
        r for r in history if r.get("record_date") and r.get("current_new_price") is not None
    ]
    auth = trend_info.get("discount_authenticity", "正常報價")

    if not valid_history:
        fig.update_layout(
            title=dict(text=f"📈 {spec_group} 新品 90 天走勢（無歷史紀錄）", font=dict(size=15)),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            annotations=[
                dict(
                    text="目前尚無此規格之新品歷史價格資料",
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=14, color="#9CA3AF"),
                )
            ],
            height=380,
            margin=dict(l=20, r=20, t=50, b=20),
        )
        return fig

    dates = [r["record_date"] for r in valid_history]
    prices = [int(r["current_new_price"]) for r in valid_history]

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=prices,
            mode="lines+markers",
            name="新品每日價格",
            line=dict(color="#38BDF8", width=3),
            marker=dict(size=6, color="#38BDF8"),
            hovertemplate="<b>日期</b>: %{x}<br><b>價格</b>: NT$ %{y:,}<extra></extra>",
        )
    )

    max_p = trend_info.get("max_price")
    min_p = trend_info.get("min_price")
    avg_p = trend_info.get("avg_price")

    if max_p is not None:
        fig.add_hline(
            y=max_p,
            line_dash="dash",
            line_color="#FF7675",
            annotation_text=f"歷史高價 ${max_p:,}",
            annotation_position="top left",
        )
    if min_p is not None:
        fig.add_hline(
            y=min_p,
            line_dash="dash",
            line_color="#34D399",
            annotation_text=f"歷史低價 ${min_p:,}",
            annotation_position="bottom left",
        )
    if avg_p is not None:
        fig.add_hline(
            y=avg_p,
            line_dash="dash",
            line_color="#9CA3AF",
            annotation_text=f"90天均價 ${int(round(avg_p)):,}",
            annotation_position="top right",
        )

    fig.update_layout(
        title=dict(text=f"📈 {spec_group} 新品 90 天走勢（{auth}）", font=dict(size=15)),
        xaxis_title="紀錄日期",
        yaxis_title="價格 (NT$)",
        hovermode="x unified",
        margin=dict(l=20, r=20, t=50, b=20),
        height=380,
    )
    return fig


def create_platform_comparison_chart(
    platform_info: dict[str, Any],
    new_min_price: int | float | None,
    spec_group: str,
) -> go.Figure:
    """繪製跨平台與賣家類型價格對比直條圖（今日新品最低價 vs FB自售 vs PTT自售 vs 工作室均價）。"""
    groups = platform_info.get("groups", {})
    fb_ind = groups.get("FB_Group_individual", {}).get("mean_price")
    ptt_ind = groups.get("PTT_individual", {}).get("mean_price")
    fb_ws = groups.get("FB_Group_workshop", {}).get("mean_price")

    categories = ["今日新品最低價", "FB 個人自售均價", "PTT 個人自售均價", "FB 工作室均價"]
    raw_vals = [new_min_price, fb_ind, ptt_ind, fb_ws]
    colors = ["#38BDF8", "#34D399", "#FBBF24", "#FF7675"]

    has_any_data = any(v is not None and v > 0 for v in raw_vals)
    if not has_any_data:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text=f"📊 {spec_group} 跨市場與賣家類型價格對比（無資料）", font=dict(size=15)),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            annotations=[
                dict(
                    text="目前尚無跨平台與賣家價格比對資料",
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=14, color="#9CA3AF"),
                )
            ],
            height=380,
            margin=dict(l=20, r=20, t=50, b=20),
        )
        return fig

    y_vals = []
    text_labels = []

    for cat, val in zip(categories, raw_vals):
        if val is not None and val > 0:
            int_val = int(round(val))
            y_vals.append(int_val)
            label = f"${int_val:,}"
            if new_min_price and new_min_price > 0 and cat != "今日新品最低價":
                diff = int_val - int(round(new_min_price))
                diff_label = f"+${diff:,}" if diff >= 0 else f"-${abs(diff):,}"
                label += f" ({diff_label})"
            text_labels.append(label)
        else:
            y_vals.append(0)
            text_labels.append("無資料")

    fig = go.Figure(
        data=[
            go.Bar(
                x=categories,
                y=y_vals,
                text=text_labels,
                textposition="auto",
                marker=dict(color=colors),
                hovertemplate="<b>類型</b>: %{x}<br><b>均價</b>: NT$ %{y:,}<extra></extra>",
            )
        ]
    )

    fig.update_layout(
        title=dict(text=f"📊 {spec_group} 跨市場與賣家類型價格對比", font=dict(size=15)),
        yaxis_title="價格 (NT$)",
        margin=dict(l=20, r=20, t=50, b=20),
        height=380,
    )
    return fig


def get_status_badge(eval_res: dict[str, Any]) -> str:
    """依行情評估結果回傳醒目的四級狀態徽章。"""
    level = eval_res.get("evaluation_level")
    if level == "bargain":
        return "🟢 低於行情推薦"
    elif level == "fair":
        return "🟡 符合行情"
    elif level == "overpriced":
        return "⚪ 偏貴買新品"
    elif level == "anomaly_low":
        return "🔴 異常低價警示"
    return "⚪ 暫無評估"


def build_listings_dataframe(
    listings: list[dict[str, Any]],
    conn: sqlite3.Connection,
    spec_group: str,
    new_min_price: int | float | None,
) -> pd.DataFrame:
    """建構二手刊登表格 DataFrame，包含四級行情評定標籤與價差分析。"""
    cols = [
        "平台",
        "發文時間",
        "賣家名稱",
        "賣家類型",
        "型號規格",
        "保固狀態",
        "開價",
        "對比行情價差",
        "新品折數",
        "行情標籤",
        "原文連結",
    ]
    if not listings:
        return pd.DataFrame(columns=cols)

    data = []
    for r in listings:
        asking = int(r.get("used_asking_price") or 0)
        eval_res = evaluate_listing_price(conn, spec_group, asking)
        badge = get_status_badge(eval_res)

        diff_amount = eval_res.get("diff_amount")
        diff_pct = eval_res.get("diff_percentage")
        if diff_amount is not None and diff_pct is not None:
            if diff_amount > 0:
                diff_str = f"+${int(round(diff_amount)):,} (+{diff_pct:.1f}%)"
            elif diff_amount < 0:
                diff_str = f"-${abs(int(round(diff_amount))):,} ({diff_pct:.1f}%)"
            else:
                diff_str = "$0 (0.0%)"
        else:
            diff_str = "-"

        if new_min_price and new_min_price > 0:
            ratio = round((asking / float(new_min_price)) * 100.0, 1)
            discount_str = f"{ratio:.1f} 折"
        else:
            discount_str = "-"

        model_name = r.get("model_name") or r.get("spec_group") or spec_group

        data.append({
            "平台": r.get("source_platform", ""),
            "發文時間": r.get("post_time", ""),
            "賣家名稱": r.get("seller_name", ""),
            "賣家類型": r.get("seller_type", "個人自售"),
            "型號規格": model_name,
            "保固狀態": r.get("warranty_status", "過保"),
            "開價": asking,
            "對比行情價差": diff_str,
            "新品折數": discount_str,
            "行情標籤": badge,
            "原文連結": r.get("post_url", ""),
        })

    return pd.DataFrame(data)


# ==============================================================================
# 3. 頁面渲染控制項 (UI Components & Layout)
# ==============================================================================


def render_sidebar(conn: sqlite3.Connection) -> tuple[str, str, bool, bool]:
    """渲染側邊欄頂部同步控制中心、過濾選單與篩選開關。"""
    # 1. 側邊欄頂部「🔄 同步控制中心」
    st.sidebar.subheader("🔄 同步控制中心")
    latest_log = get_latest_sync_log(conn)
    if latest_log:
        raw_time = str(latest_log.get("sync_time", ""))
        fmt_time = raw_time.replace("T", " ").split(".")[0] if raw_time else "未知時間"
        status = latest_log.get("status")
        status_badge = "🟢 同步成功" if status == "SUCCESS" else "🔴 同步失敗"
        coolpc_cnt = latest_log.get("coolpc_count", 0)
        ptt_cnt = latest_log.get("ptt_count", 0)
        fb_cnt = latest_log.get("fb_count", 0)

        st.sidebar.caption(f"**狀態**：{status_badge}  \n**時間**：`{fmt_time}`")
        st.sidebar.caption(f"📊 **摘要**：原價屋: {coolpc_cnt} 筆 · PTT: {ptt_cnt} 筆 · FB: {fb_cnt} 筆")
    else:
        st.sidebar.caption("**狀態**：⚪ 尚未執行同步  \n**時間**：無紀錄")

    if st.sidebar.button("🔄 立即同步", use_container_width=True):
        with st.spinner("正在同步最新市場數據..."):
            try:
                execute_sync(conn)
                st.toast("✅ 資料同步完成！")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"同步失敗: {e}")

    st.sidebar.markdown("---")

    # 2. 篩選控制項
    st.sidebar.header("🔍 篩選與控制台")

    # 零組件大類選單
    category = st.sidebar.selectbox("零組件類別", CATEGORIES, index=0)

    # 規格群組動態下拉
    spec_groups = get_spec_groups(conn, category)
    spec_group = st.sidebar.selectbox("規格群組 (spec_group)", spec_groups, index=0)

    st.sidebar.markdown("---")

    # 過濾開關
    exclude_workshops = st.sidebar.checkbox("排除商業工作室貼文", value=True)
    only_in_warranty = st.sidebar.checkbox("僅看原廠保固內商品", value=False)

    return category, spec_group, exclude_workshops, only_in_warranty


def render_metrics(metric_data: dict[str, Any]) -> None:
    """渲染頂部 4 大重點指標卡。"""
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            label="今日新品最低價",
            value=metric_data["card1_val"],
            delta=metric_data["card1_delta"],
            delta_color="off",
        )
    with c2:
        st.metric(
            label="二手市場中位價",
            value=metric_data["card2_val"],
            delta=metric_data["card2_delta"],
            delta_color="off",
            help="優先採個人自售中位數，無自售時採全體二手貼文中位數",
        )
    with c3:
        st.metric(
            label="二手折數",
            value=metric_data["card3_val"],
            delta=metric_data["card3_delta"],
            delta_color="off",
        )
    with c4:
        st.metric(
            label="轉買二手可省金額",
            value=metric_data["card4_val"],
            delta=metric_data["card4_delta"],
            delta_color="off",
        )


def render_charts(trend_fig: go.Figure, platform_fig: go.Figure) -> None:
    """渲染中段 2 大 Plotly 視覺化圖表。"""
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(trend_fig, use_container_width=True)
    with c2:
        st.plotly_chart(platform_fig, use_container_width=True)


def render_table(df: pd.DataFrame) -> None:
    """渲染即時二手比價表格。"""
    st.dataframe(
        df,
        column_config={
            "原文連結": st.column_config.LinkColumn("原文連結", display_text="查看貼文"),
            "開價": st.column_config.NumberColumn("開價", format="$%d"),
        },
        hide_index=True,
        use_container_width=True,
    )


def main(db_path: str = "data/hardware.db") -> None:
    """Streamlit 應用程式進入點。"""
    try:
        st.set_page_config(
            page_title="電腦硬體新品與二手市場比價分析系統",
            layout="wide",
            page_icon="💻",
        )
    except StreamlitAPIException:
        pass

    st.title("💻 電腦硬體新品與二手市場比價分析系統")
    st.markdown("即時整合原價屋新品報價、PTT HardwareSale 與 FB 二手社團行情，精準評估折數與真假降價。")

    # 1. 建立資料庫連線
    conn = get_db(db_path)

    # 2. 伺服器啟動自動同步 (啟動防重保護，僅執行一次)
    if "auto_sync_done" not in st.session_state:
        st.session_state["auto_sync_done"] = True
        if not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("ENABLE_TEST_AUTO_SYNC"):
            try:
                execute_sync(conn)
            except Exception as e:
                st.sidebar.error(f"自動同步失敗: {e}")

    # 3. 側邊欄過濾控制項
    _, spec_group, exclude_workshops, only_in_warranty = render_sidebar(conn)

    # 3. 查詢核心數據
    discount_info = calculate_spec_group_discount(conn, spec_group)
    rep_prod = get_representative_product(conn, spec_group)
    if rep_prod:
        trend_info = analyze_new_price_trend(conn, rep_prod["product_id"], days=90)
    else:
        trend_info = {
            "product_id": None,
            "max_price": None,
            "min_price": None,
            "avg_price": None,
            "current_price": None,
            "price_percentile": None,
            "discount_authenticity": "無歷史資料",
            "avg_30d_price": None,
            "price_history": [],
        }

    platform_info = compare_platforms_and_sellers(conn, spec_group)

    # 4. 頂部 4 大重點指標卡
    st.subheader(f"📌 {spec_group} 核心行情指標")
    metric_data = format_metric_card_data(trend_info, discount_info)
    render_metrics(metric_data)

    st.markdown("---")

    # 5. 中段 2 大 Plotly 視覺化圖表
    trend_fig = create_price_trend_chart(trend_info, spec_group)
    platform_fig = create_platform_comparison_chart(
        platform_info, discount_info.get("new_min_price"), spec_group
    )
    render_charts(trend_fig, platform_fig)

    st.markdown("---")

    # 6. 下方即時二手比價列表
    st.subheader(f"🛒 {spec_group} 即時二手刊登列表")
    listings = get_filtered_listings(
        conn,
        spec_group,
        exclude_workshops=exclude_workshops,
        only_in_warranty=only_in_warranty,
    )
    if not listings:
        st.info("目前尚無此規格之二手貼文資料，請點擊側邊欄同步按鈕或先執行爬蟲入庫。")
    else:
        df = build_listings_dataframe(
            listings, conn, spec_group, discount_info.get("new_min_price")
        )
        render_table(df)


if __name__ == "__main__":
    main()

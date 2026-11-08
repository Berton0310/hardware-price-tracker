"""資料庫重新清洗與二手貼文結構化重解析模組 (cleaners/resanitize_db.py)

遵循 CLAUDE.md、AGENTS.md 與「刪到不能再刪」極簡標準。
職責：
1. 清除 products 中污染 GPU 最低新品價的周邊配件（如 $190 麗臺 mini-DP 轉接線）。
2. 重新解析 data/hardware.db 中的二手貼文，修復昔日因 OpenRouter 模型失效或 Fallback 缺失
   導致的 '[賣'、'Facebook' 標籤與 0 元售價。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

# 確保專案根目錄納入模組搜尋路徑
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import bs4
from cleaners.llm_parser import parse_post_with_llm
from crawlers.ptt_crawler import _get_ptt_session, _match_product_id, fetch_ptt_post_details
from database.db_manager import init_db

logger = logging.getLogger("resanitize_db")

DEFAULT_DB_PATH = "data/hardware.db"

# FB 貼文已知對映字典（修復早期爬蟲將發文者與內文切分導致的 Facebook 標籤）
FB_KNOWN_REPAIRS: dict[str, dict[str, Any]] = {
    "FB_c80db8157c57": {"spec_group": "RTX 4090 24G", "brand": "Generic", "used_asking_price": 52000},
    "FB_ffb4cf73de1d": {"spec_group": "i5-11300H 筆電", "brand": "Generic", "used_asking_price": 15800},
    "FB_4fae3d0c3c6b": {"spec_group": "GTX 1650 筆電", "brand": "Lenovo", "used_asking_price": 7000},
    "FB_cbad665eff59": {"spec_group": "RTX 2060 6G", "brand": "ASUS", "used_asking_price": 7000},
    "FB_109c2ede9515": {"spec_group": "RTX 3060 12G", "brand": "MSI", "used_asking_price": 13000},
    "FB_7d1f1033e754": {"spec_group": "GTX 1660 6G", "brand": "Gigabyte", "used_asking_price": 4500},
    "FB_032c253ca0f3": {"spec_group": "RTX 4090 24G", "brand": "Generic", "used_asking_price": 52000},
    "FB_62bf0d7357da": {"spec_group": "GTX 1060 6G", "brand": "MSI", "used_asking_price": 2500},
    "FB_2599e881dd42": {"spec_group": "RTX 3070 8G", "brand": "ASUS", "used_asking_price": 9500},
    "FB_32d10ef08b81": {"spec_group": "RTX 4070 12G", "brand": "Gigabyte", "used_asking_price": 17500},
    "FB_745258d5b333": {"spec_group": "RTX 3060 Ti 8G", "brand": "ZOTAC", "used_asking_price": 8000},
    "FB_1a40b4a7f843": {"spec_group": "RTX 2060 6G", "brand": "ASUS", "used_asking_price": 7000},
    "FB_a3eb25bb8c04": {"spec_group": "DDR4-3200 16G", "brand": "Kingston", "used_asking_price": 1200},
}


def clean_polluted_products(conn: sqlite3.Connection) -> int:
    """清除 products 中污染 GPU 價格之周邊配件（如 $190 mini-DP 轉接線、顯卡支架等）。"""
    cur = conn.execute(
        """
        SELECT product_id, model_name
        FROM products
        WHERE (category = 'GPU' AND model_name LIKE '%轉接%')
           OR (category = 'GPU' AND product_id IN (
               SELECT product_id FROM new_price_history WHERE current_new_price < 500
           ));
        """
    )
    polluted = cur.fetchall()
    deleted_count = 0
    for row in polluted:
        p_id = row[0]
        conn.execute("DELETE FROM products WHERE product_id = ?;", (p_id,))
        deleted_count += 1
    conn.commit()
    return deleted_count


def resanitize_ptt_listings(conn: sqlite3.Connection, delay: float = 0.5) -> int:
    """重新抓取並以升級後的 LLM/正則解析器重構 PTT 二手貼文。"""
    cur = conn.execute(
        """
        SELECT post_id, post_url, used_asking_price, spec_group
        FROM used_market_listings
        WHERE source_platform = 'PTT HardwareSale';
        """
    )
    rows = cur.fetchall()
    session = _get_ptt_session()
    updated_count = 0

    for r in rows:
        post_id = str(r[0])
        post_url = str(r[1])
        title = ""
        content = ""

        # 連線 PTT 抓取最新內文 (具備 3 次重試防斷線機制)
        for attempt in range(3):
            try:
                resp = session.get(post_url, timeout=10)
                if resp.status_code == 200:
                    soup = bs4.BeautifulSoup(resp.text, "html.parser")
                    t_meta = soup.find("meta", property="og:title")
                    title = t_meta["content"] if t_meta else ""
                    det = fetch_ptt_post_details(post_url, session=session)
                    content = det["content"]
                    break
            except Exception:
                time.sleep(1.0)

        # 若成功取得或降級為空
        parsed = parse_post_with_llm(title, content)
        spec = parsed.get("spec_group") or "Generic-Hardware"
        if spec.startswith("["):
            spec = re.sub(r"^\[[^\]]+\]\s*", "", spec).strip() or "Generic-Hardware"

        price = parsed.get("used_asking_price", 0)
        prod_id = _match_product_id(conn, spec)
        sale_status = "已售出" if re.search(r"(已售|售出|完售)", title) else "販售中"

        conn.execute(
            """
            UPDATE used_market_listings
            SET spec_group = ?,
                product_id = ?,
                seller_type = ?,
                used_asking_price = ?,
                warranty_status = ?,
                trade_method = ?,
                sale_status = ?
            WHERE post_id = ?;
            """,
            (
                spec,
                prod_id,
                parsed.get("seller_type", "個人自售"),
                price,
                parsed.get("warranty_status", "過保"),
                parsed.get("trade_method", "面交 / 郵寄"),
                sale_status,
                post_id,
            ),
        )
        updated_count += 1
        time.sleep(delay)

    conn.commit()
    return updated_count


def resanitize_fb_listings(conn: sqlite3.Connection) -> int:
    """清洗 FB 貼文中殘存的 'Facebook'、'社團動態消息排序方式' 與亂碼標籤。"""
    cur = conn.execute(
        """
        SELECT post_id, spec_group, used_asking_price, seller_name
        FROM used_market_listings
        WHERE source_platform LIKE '%Facebook%' OR source_platform LIKE '%FB%';
        """
    )
    rows = cur.fetchall()
    updated_count = 0

    for r in rows:
        post_id = str(r[0])
        old_spec = str(r[1])
        old_price = int(r[2] or 0)
        seller = str(r[3] or "")

        new_spec = old_spec
        new_price = old_price

        # 1. 套用已知修復表
        if post_id in FB_KNOWN_REPAIRS:
            repair = FB_KNOWN_REPAIRS[post_id]
            new_spec = repair["spec_group"]
            if old_price <= 0 or old_price > 100000:
                new_price = repair["used_asking_price"]

        # 2. 清理亂碼前綴與括號
        if new_spec.startswith("【") or new_spec.startswith("（") or new_spec.startswith("["):
            new_spec = re.sub(r"^[【（\[][^】）\]]*[】）\]]\s*", "", new_spec).strip()

        # 3. 處理介面噪音 (如社團動態排序、Facebook等)
        if new_spec.lower() in ("facebook", "社團動態消息排序方式", ""):
            if "asus" in seller.lower() or "tuf" in seller.lower():
                new_spec = "ASUS TUF F15 FX507ZC4"
            elif "lenovo" in seller.lower() or "ideapad" in seller.lower():
                new_spec = "ideapad gaming 15ihu6 (GTX 1650)"
            elif old_price > 0:
                new_spec = "二手電腦主機/零件"
            else:
                new_spec = "其他硬體周邊"

        # 4. 價格合理性修整（過濾異常百萬/防呆標價，例如 162000 -> 16200）
        if new_price == 162000:
            new_price = 16200
        elif new_price == 162:
            new_price = 16200

        # 反查 product_id
        prod_id = _match_product_id(conn, new_spec)

        conn.execute(
            """
            UPDATE used_market_listings
            SET spec_group = ?,
                product_id = ?,
                used_asking_price = ?
            WHERE post_id = ?;
            """,
            (new_spec, prod_id, new_price, post_id),
        )
        updated_count += 1

    conn.commit()
    return updated_count


def resanitize_database(db_path: str = DEFAULT_DB_PATH) -> dict[str, int]:
    """一鍵執行資料庫全面清洗重解析。"""
    print(f"[*] 開始執行硬體資料庫全量清洗 (DB: {db_path})...")
    conn = init_db(db_path)
    try:
        # 1. 清除原價屋污染顯卡價格之轉接線配件
        deleted_prods = clean_polluted_products(conn)
        print(f"  [1/3] 已清除 {deleted_prods} 筆非顯卡轉接線/配件資料。")

        # 2. 重新解析 PTT 二手貼文
        updated_ptt = resanitize_ptt_listings(conn, delay=0.4)
        print(f"  [2/3] 已完成 {updated_ptt} 筆 PTT 貼文重構與價格回填。")

        # 3. 清洗與修復 FB 二手貼文
        updated_fb = resanitize_fb_listings(conn)
        print(f"  [3/3] 已完成 {updated_fb} 筆 FB 貼文標籤與價格修復。")

        return {
            "deleted_products": deleted_prods,
            "updated_ptt": updated_ptt,
            "updated_fb": updated_fb,
            "total_used": updated_ptt + updated_fb,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    res = resanitize_database(db_file)
    print("\n[OK] 資料庫清洗完成！摘要：", res)

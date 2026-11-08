"""資料庫連線、初始化與 CRUD 模組 (database/db_manager.py)

採用 Python 內建 sqlite3 標準函式庫，零外部依賴。
遵循「刪到不能再刪」極簡標準。
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any


def init_db(db_path: str = "data/hardware.db") -> sqlite3.Connection:
    """初始化 SQLite 資料庫連線，開啟外鍵約束與 WAL 模式，並建立資料表與索引。"""
    if db_path != ":memory:":
        dir_name = os.path.dirname(os.path.abspath(db_path))
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            spec_group TEXT NOT NULL,
            brand TEXT NOT NULL,
            model_name TEXT NOT NULL UNIQUE,
            key_specs TEXT,
            warranty_years INTEGER DEFAULT 3
        );
        CREATE INDEX IF NOT EXISTS idx_products_spec_group ON products(spec_group);

        CREATE TABLE IF NOT EXISTS new_price_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            record_date TEXT NOT NULL,
            listed_original_price INTEGER NOT NULL,
            current_new_price INTEGER NOT NULL,
            price_change_tag TEXT NOT NULL,
            in_stock INTEGER DEFAULT 1,
            UNIQUE(product_id, record_date)
        );
        CREATE INDEX IF NOT EXISTS idx_price_date ON new_price_history(record_date);

        CREATE TABLE IF NOT EXISTS used_market_listings (
            post_id TEXT PRIMARY KEY,
            product_id INTEGER REFERENCES products(product_id) ON DELETE SET NULL,
            spec_group TEXT NOT NULL,
            source_platform TEXT NOT NULL,
            seller_name TEXT NOT NULL,
            seller_type TEXT NOT NULL,
            used_asking_price INTEGER NOT NULL,
            warranty_status TEXT NOT NULL,
            trade_method TEXT,
            comment_count INTEGER DEFAULT 0,
            sale_status TEXT NOT NULL,
            post_time TEXT NOT NULL,
            post_url TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_used_spec_group ON used_market_listings(spec_group);
        CREATE INDEX IF NOT EXISTS idx_used_post_time ON used_market_listings(post_time);

        CREATE TABLE IF NOT EXISTS sync_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_time TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            coolpc_count INTEGER DEFAULT 0,
            ptt_count INTEGER DEFAULT 0,
            fb_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sync_logs_time ON sync_logs(sync_time);
        """
    )
    conn.commit()
    return conn


def upsert_product(
    conn: sqlite3.Connection,
    category: str,
    spec_group: str,
    brand: str,
    model_name: str,
    key_specs: str = "",
    warranty_years: int = 3,
) -> int:
    """插入或更新產品主檔，回傳 product_id。"""
    cursor = conn.execute(
        """
        INSERT INTO products (category, spec_group, brand, model_name, key_specs, warranty_years)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(model_name) DO UPDATE SET
            category = excluded.category,
            spec_group = excluded.spec_group,
            brand = excluded.brand,
            key_specs = excluded.key_specs,
            warranty_years = excluded.warranty_years
        RETURNING product_id;
        """,
        (category, spec_group, brand, model_name, key_specs, int(warranty_years)),
    )
    product_id = cursor.fetchone()[0]
    conn.commit()
    return int(product_id)


def record_new_price(
    conn: sqlite3.Connection,
    product_id: int,
    record_date: str,
    listed_price: int,
    current_price: int,
    change_tag: str = "不變",
    in_stock: bool | int = True,
) -> None:
    """記錄新品每日歷史價格，同一天同一產品若已存在則更新價格與狀態。"""
    stock_val = 1 if in_stock else 0
    conn.execute(
        """
        INSERT INTO new_price_history (
            product_id, record_date, listed_original_price, current_new_price, price_change_tag, in_stock
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, record_date) DO UPDATE SET
            listed_original_price = excluded.listed_original_price,
            current_new_price = excluded.current_new_price,
            price_change_tag = excluded.price_change_tag,
            in_stock = excluded.in_stock;
        """,
        (
            int(product_id),
            record_date,
            int(listed_price),
            int(current_price),
            change_tag,
            stock_val,
        ),
    )
    conn.commit()


def upsert_used_listing(conn: sqlite3.Connection, post_dict: dict[str, Any]) -> None:
    """插入或更新二手市場貼文與行情記錄。"""
    conn.execute(
        """
        INSERT INTO used_market_listings (
            post_id, product_id, spec_group, source_platform, seller_name, seller_type,
            used_asking_price, warranty_status, trade_method, comment_count,
            sale_status, post_time, post_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            product_id = excluded.product_id,
            spec_group = excluded.spec_group,
            source_platform = excluded.source_platform,
            seller_name = excluded.seller_name,
            seller_type = excluded.seller_type,
            used_asking_price = excluded.used_asking_price,
            warranty_status = excluded.warranty_status,
            trade_method = excluded.trade_method,
            comment_count = excluded.comment_count,
            sale_status = excluded.sale_status,
            post_time = excluded.post_time,
            post_url = excluded.post_url;
        """,
        (
            str(post_dict["post_id"]),
            post_dict.get("product_id"),
            str(post_dict["spec_group"]),
            str(post_dict["source_platform"]),
            str(post_dict["seller_name"]),
            str(post_dict.get("seller_type", "個人自售")),
            int(post_dict["used_asking_price"]),
            str(post_dict.get("warranty_status", "過保")),
            post_dict.get("trade_method"),
            int(post_dict.get("comment_count", 0) or 0),
            str(post_dict.get("sale_status", "販售中")),
            str(post_dict["post_time"]),
            str(post_dict.get("post_url", "")),
        ),
    )
    conn.commit()


def get_product_price_history(
    conn: sqlite3.Connection, product_id: int, days: int = 90
) -> list[dict[str, Any]]:
    """查詢某產品近 N 日價格軌跡。若 days <= 0 則回傳所有歷史紀錄。"""
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
    cols = [col[0] for col in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_used_market_by_spec(
    conn: sqlite3.Connection, spec_group: str
) -> list[dict[str, Any]]:
    """查詢某規格群組之二手市場貼文列表。"""
    cursor = conn.execute(
        """
        SELECT post_id, product_id, spec_group, source_platform, seller_name, seller_type,
               used_asking_price, warranty_status, trade_method, comment_count,
               sale_status, post_time, post_url
        FROM used_market_listings
        WHERE spec_group = ?
        ORDER BY post_time DESC;
        """,
        (str(spec_group),),
    )
    cols = [col[0] for col in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def record_sync_log(
    conn: sqlite3.Connection,
    status: str,
    details: str = "",
    coolpc_count: int = 0,
    ptt_count: int = 0,
    fb_count: int = 0,
    sync_time: str | None = None,
) -> int:
    """記錄資料同步日誌至 sync_logs 資料表，回傳 log_id。"""
    if sync_time is None:
        sync_time = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        """
        INSERT INTO sync_logs (sync_time, status, details, coolpc_count, ptt_count, fb_count)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING log_id;
        """,
        (
            str(sync_time),
            str(status),
            str(details),
            int(coolpc_count),
            int(ptt_count),
            int(fb_count),
        ),
    )
    log_id = cursor.fetchone()[0]
    conn.commit()
    return int(log_id)


def get_latest_sync_log(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """查詢最新一筆同步日誌記錄；若無任何記錄則回傳 None。"""
    cursor = conn.execute(
        """
        SELECT log_id, sync_time, status, details, coolpc_count, ptt_count, fb_count
        FROM sync_logs
        ORDER BY sync_time DESC, log_id DESC
        LIMIT 1;
        """
    )
    row = cursor.fetchone()
    if not row:
        return None
    cols = [col[0] for col in cursor.description] if cursor.description else []
    return dict(zip(cols, row))


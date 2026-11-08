"""即時資料同步核心服務模組 (services/sync_service.py)

遵循 CLAUDE.md、AGENTS.md 與「刪到不能再刪」極簡標準。
整合原價屋新品、PTT 二手板與 Facebook 社團（依 Cookie 健康狀態判定）之一鍵與自動同步。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from crawlers import coolpc_crawler, fb_crawler, ptt_crawler
from database.db_manager import record_sync_log

logger = logging.getLogger("sync_service")

# 預設 Facebook 二手硬體社團清單
DEFAULT_FB_GROUPS = [
    "https://www.facebook.com/groups/2725975797677135/?locale=zh_TW",
]


def execute_sync(
    conn: sqlite3.Connection,
    include_fb: bool = True,
    ptt_pages: int = 1,
    fb_max_posts: int = 5,
    cookie_path: str = "data/fb_cookies.json",
) -> dict[str, Any]:
    """執行全平台即時資料同步。

    1. 同步原價屋新品歷史報價 (CoolPC)。
    2. 同步 PTT HardwareSale 二手板最新刊登貼文。
    3. 檢測本地 FB Cookie：若有效且 include_fb 為 True 則抓取社團貼文；若無或異常則優雅跳過。
    4. 將各平台抓取筆數與執行狀態持久化至 sync_logs 資料表。
    """
    coolpc_count = 0
    ptt_count = 0
    fb_count = 0
    errors: list[str] = []

    # 1. 原價屋新品同步
    try:
        coolpc_count = coolpc_crawler.sync_coolpc_to_db(conn)
    except Exception as e:
        logger.warning(f"原價屋同步異常: {e}")
        errors.append(f"原價屋: {e}")

    # 2. PTT 二手板同步
    try:
        ptt_count = ptt_crawler.sync_ptt_to_db(conn, max_pages=ptt_pages)
    except Exception as e:
        logger.warning(f"PTT 同步異常: {e}")
        errors.append(f"PTT: {e}")

    # 3. Facebook 社團同步 (僅在 Cookie 有效時執行，若無或異常則優雅跳過)
    if include_fb:
        # 測試環境下預設跳過真實 Playwright 瀏覽器啟動以確保測試毫秒級完成
        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ENABLE_FB_TEST_CRAWL"):
            logger.info("檢測到測試環境，預設跳過 Facebook 真實瀏覽器爬取。")
        else:
            try:
                if fb_crawler.check_fb_cookie_health(cookie_path):
                    for group_url in DEFAULT_FB_GROUPS:
                        try:
                            c = fb_crawler.sync_fb_to_db(
                                conn,
                                group_url=group_url,
                                max_posts=fb_max_posts,
                                cookie_path=cookie_path,
                            )
                            fb_count += c
                        except Exception as fe:
                            logger.warning(f"FB 社團同步異常（優雅跳過）：{fe}")
                else:
                    logger.info("Facebook Cookie 無效或不存在，優雅跳過社團爬取。")
            except Exception as e:
                logger.warning(f"Facebook Cookie 檢測異常（優雅跳過）：{e}")

    # 4. 判定同步狀態與記錄
    # 若有錯誤且無任何核心數據更新，或原價屋新品失敗，判定為 FAILED
    status = "FAILED" if errors else "SUCCESS"
    details = f"原價屋: {coolpc_count} 筆, PTT: {ptt_count} 筆, FB: {fb_count} 筆"
    if errors:
        details += f" (異常: {'; '.join(errors)})"

    try:
        log_id = record_sync_log(
            conn=conn,
            status=status,
            details=details,
            coolpc_count=coolpc_count,
            ptt_count=ptt_count,
            fb_count=fb_count,
        )
    except Exception as e:
        logger.error(f"寫入 sync_log 失敗: {e}")
        log_id = -1

    if status == "FAILED":
        raise RuntimeError(f"同步失敗: {'; '.join(errors)}")

    return {
        "status": status,
        "coolpc_count": coolpc_count,
        "ptt_count": ptt_count,
        "fb_count": fb_count,
        "details": details,
        "log_id": log_id,
    }

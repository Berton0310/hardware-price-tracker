"""Facebook 二手硬體社團爬蟲模組 (crawlers/fb_crawler.py)

遵循「刪到不能再刪」極簡標準。
使用 Playwright 模擬真人行為爬取 Facebook 社團貼文。
具備 Pre-flight Cookie 健康檢查、真人模擬捲動（600~900px, 2.5~4.5s 隨機延遲）與優雅降級機制。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import time
from typing import Any, Callable

from cleaners.llm_parser import parse_post_with_llm
from database.db_manager import upsert_used_listing

logger = logging.getLogger("fb_crawler")

DEFAULT_COOKIE_PATH = "data/fb_cookies.json"
ALT_COOKIE_PATH = "data/www.facebook.com_cookies.txt"
FB_BASE_URL = "https://www.facebook.com"


def _parse_cookie_file(cookie_path: str) -> list[dict[str, Any]]:
    """解析 Cookie 檔案，相容 JSON 陣列與 Netscape .txt 格式。"""
    actual_path = cookie_path
    # 僅在未指定自訂路徑 (使用預設路徑) 且預設檔不存在時，嘗試回退至備用 .txt 檔案
    if cookie_path == DEFAULT_COOKIE_PATH and not os.path.isfile(actual_path) and os.path.isfile(ALT_COOKIE_PATH):
        actual_path = ALT_COOKIE_PATH

    if not os.path.isfile(actual_path):
        return []

    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        # 嘗試 JSON 解析
        if content.startswith("[") or content.startswith("{"):
            data = json.loads(content)
            return data if isinstance(data, list) else [data]

        # 嘗試 Netscape .txt 分割解析
        cookies = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, _, path, secure, expires, name, value = parts[:7]
                c_dict: dict[str, Any] = {
                    "domain": domain,
                    "path": path,
                    "secure": secure.lower() == "true",
                    "name": name,
                    "value": value,
                }
                try:
                    c_dict["expires"] = float(expires)
                except Exception:
                    pass
                cookies.append(c_dict)
        return cookies
    except Exception as e:
        logger.warning(f"讀取 Cookie 檔案失敗 ({actual_path})：{e}")
        return []


def check_fb_cookie_health(cookie_path: str = DEFAULT_COOKIE_PATH) -> bool:
    """Pre-flight 檢查本地 Cookie 檔案是否存在且含有登入授權憑證。"""
    cookies = _parse_cookie_file(cookie_path)
    if not cookies:
        return False

    has_c_user = any(c.get("name") == "c_user" and bool(c.get("value")) for c in cookies)
    has_xs = any(c.get("name") == "xs" and bool(c.get("value")) for c in cookies)
    return has_c_user or has_xs


def _load_cookies_into_context(context: Any, cookie_path: str) -> bool:
    """載入 Cookie 至 Playwright Browser Context。"""
    cookies = _parse_cookie_file(cookie_path)
    if not cookies:
        return False
    try:
        context.add_cookies(cookies)
        return True
    except Exception as e:
        logger.warning(f"載入 Cookie 至 Context 失敗：{e}")
        return False


def crawl_fb_group(
    group_url: str,
    max_posts: int = 10,
    cookie_path: str = DEFAULT_COOKIE_PATH,
    headless: bool = True,
    timeout: int = 30000,
) -> list[dict[str, Any]]:
    """爬取 Facebook 社團貼文清單，若無 Cookie 則自動切換為公開模式或優雅返回。"""
    # 延遲導入 Playwright 避免在未安裝環境中直接報錯
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("未安裝 Playwright，跳過 Facebook 社團爬蟲。")
        return []

    is_healthy = check_fb_cookie_health(cookie_path)
    if not is_healthy:
        logger.warning(
            f"[FB Crawler Warning] Cookie 檔案不存在或無效 ({cookie_path})，"
            "將嘗試以公開模式 (Public Mode) 瀏覽；若社團需要登入則安全返回。"
        )

    posts: list[dict[str, Any]] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="zh-TW",
            )

            if is_healthy:
                _load_cookies_into_context(context, cookie_path)

            page = context.new_page()

            try:
                page.goto(group_url, timeout=timeout, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"導向社團頁面超時或異常：{e}")
                browser.close()
                return []

            # 檢測是否被強制重導向至登入頁且處於公開模式
            if "facebook.com/login" in page.url and not is_healthy:
                logger.warning("社團要求強制登入，因無有效 Cookie 優雅返回空資料。")
                browser.close()
                return []

            time.sleep(2)

            # 關閉可能彈出的「稍後再說」登入對話框
            try:
                close_btn = page.query_selector('div[aria-label="關閉"], div[aria-label="Close"]')
                if close_btn:
                    close_btn.click()
            except Exception:
                pass

            # 真人捲動與貼文收集
            scroll_count = 0
            max_scrolls = max(3, max_posts // 2 + 2)

            while len(posts) < max_posts and scroll_count < max_scrolls:
                scroll_count += 1

                # 尋找貼文區塊
                feed_items = page.query_selector_all('div[role="feed"] > div, div[data-ad-preview="message"]')

                for item in feed_items:
                    text = item.inner_text().strip()
                    if len(text) < 15:
                        continue

                    # 過濾 FB 介面雜訊（導覽標籤、建立貼文、排序選單等非貼文元素）
                    first_line = text.split("\n")[0].strip()
                    if first_line in ("Facebook", "社團動態消息排序方式", "建立貼文", "熱門貼文", "最新動態"):
                        continue
                    if text.startswith("Facebook\n") or text == "Facebook":
                        continue

                    # 避免重複貼文
                    text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                    if any(p["hash"] == text_hash for p in posts):
                        continue

                    # 嘗試抓取貼文連結
                    post_link = ""
                    a_elem = item.query_selector('a[href*="/posts/"], a[href*="/permalink/"]')
                    if a_elem:
                        href = a_elem.get_attribute("href") or ""
                        post_link = href.split("?")[0]
                        if post_link.startswith("/"):
                            post_link = f"{FB_BASE_URL}{post_link}"

                    post_id = f"FB_{text_hash[:12]}"

                    # 嘗試抓取發文者
                    author_elem = item.query_selector('h2 a, strong a, span[dir="auto"] strong')
                    author = author_elem.inner_text().strip() if author_elem else "FB_User"

                    posts.append({
                        "post_id": post_id,
                        "hash": text_hash,
                        "title": text[:60].replace("\n", " "),
                        "content": text,
                        "post_url": post_link or group_url,
                        "author": author,
                        "post_time": datetime.datetime.now().isoformat(),
                    })

                    if len(posts) >= max_posts:
                        break

                # 真人模擬捲動：每次 600~900px，隨機等待 2.5 ~ 4.5 秒
                scroll_delta = random.randint(600, 900)
                page.evaluate(f"window.scrollBy(0, {scroll_delta});")
                sleep_time = random.uniform(2.5, 4.5)
                time.sleep(sleep_time)

            browser.close()
    except Exception as e:
        logger.warning(f"Facebook 爬取過程發生異常（已安全捕獲）：{e}")
        return posts

    return posts


def sync_fb_to_db(
    conn: sqlite3.Connection,
    group_url: str,
    max_posts: int = 10,
    cookie_path: str = DEFAULT_COOKIE_PATH,
    llm_parser_func: Callable[[str, str], dict[str, Any]] | None = None,
) -> int:
    """抓取 Facebook 社團貼文，結構化清洗後同步至 used_market_listings。"""
    parser = llm_parser_func or parse_post_with_llm
    posts = crawl_fb_group(group_url=group_url, max_posts=max_posts, cookie_path=cookie_path)
    synced_count = 0

    for p in posts:
        post_id = p["post_id"]

        # 快取防重複
        cur = conn.execute("SELECT 1 FROM used_market_listings WHERE post_id = ?;", (post_id,))
        if cur.fetchone():
            continue

        parsed = parser(p["title"], p["content"])

        # 排除非硬體貼文或 UI 雜訊
        spec_group = parsed.get("spec_group", "").strip()
        if spec_group.lower() in ("facebook", "unknown", "other", "建立貼文", "社團動態消息排序方式") and parsed.get("used_asking_price", 0) <= 0:
            continue

        # 反查 product_id
        cur_prod = conn.execute(
            "SELECT product_id FROM products WHERE spec_group = ? OR spec_group LIKE ? LIMIT 1;",
            (spec_group, f"%{spec_group}%"),
        )
        row = cur_prod.fetchone()
        prod_id = int(row[0]) if row else None

        sale_status = "已售出" if re.search(r"(已售|售出|完售)", p["title"]) else "販售中"

        listing_dict = {
            "post_id": post_id,
            "product_id": prod_id,
            "spec_group": spec_group or "Unknown",
            "source_platform": "Facebook Group",
            "seller_name": p["author"],
            "seller_type": parsed.get("seller_type", "個人自售"),
            "used_asking_price": parsed.get("used_asking_price", 0),
            "warranty_status": parsed.get("warranty_status", "過保"),
            "trade_method": parsed.get("trade_method", "面交 / 郵寄"),
            "comment_count": 0,
            "sale_status": sale_status,
            "post_time": p["post_time"],
            "post_url": p["post_url"],
        }

        upsert_used_listing(conn, listing_dict)
        synced_count += 1

    return synced_count

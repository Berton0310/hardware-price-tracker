"""PTT HardwareSale 二手板爬蟲模組 (crawlers/ptt_crawler.py)

遵循「刪到不能再刪」極簡標準。
負責抓取 PTT 電腦硬體二手板（HardwareSale）靜態 HTML，篩選 [賣/ 貼文，
呼叫 llm_parser 進行結構化抽取，並同步入庫至 used_market_listings。
"""

from __future__ import annotations

import datetime
import random
import re
import sqlite3
import time
from typing import Any, Callable

import bs4
import requests

from cleaners.llm_parser import parse_post_with_llm
from database.db_manager import upsert_used_listing

PTT_BASE_URL = "https://www.ptt.cc"
HARDWARESALE_URL = f"{PTT_BASE_URL}/bbs/HardwareSale/index.html"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get_ptt_session() -> requests.Session:
    """建立帶有 over18=1 Cookie 的 Session。"""
    s = requests.Session()
    s.cookies.set("over18", "1", domain=".ptt.cc")
    s.headers.update(DEFAULT_HEADERS)
    return s


def parse_ptt_date(date_str: str) -> str:
    """解析 PTT 時間字串為 ISO 格式。"""
    date_str = date_str.strip()
    # 範例：Tue Nov  3 14:17:31 2026
    # 處理雙空格或單空格
    cleaned = re.sub(r"\s+", " ", date_str)
    try:
        dt = datetime.datetime.strptime(cleaned, "%a %b %d %H:%M:%S %Y")
        return dt.isoformat()
    except Exception:
        return datetime.datetime.now().isoformat()


def fetch_ptt_page(url: str, session: requests.Session | None = None, timeout: int = 10) -> tuple[str, str | None]:
    """抓取單頁 PTT 文章列表，回傳 (html_string, prev_page_url)。"""
    s = session or _get_ptt_session()
    resp = s.get(url, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    soup = bs4.BeautifulSoup(html, "html.parser")
    prev_url: str | None = None
    # 尋找「‹ 上頁」按鈕
    for a in soup.find_all("a", class_="btn wide"):
        if "上頁" in a.text:
            href = a.get("href")
            if href:
                prev_url = f"{PTT_BASE_URL}{href}"
            break

    return html, prev_url


def parse_ptt_post_list(html: str) -> list[dict[str, Any]]:
    """從 PTT 看板 HTML 中解析出符合 [賣/ 條件之貼文清單。"""
    soup = bs4.BeautifulSoup(html, "html.parser")
    posts: list[dict[str, Any]] = []

    for rent in soup.find_all("div", class_="r-ent"):
        title_tag = rent.find("div", class_="title")
        if not title_tag:
            continue

        a_tag = title_tag.find("a")
        if not a_tag:
            # 可能是已被刪除文章
            continue

        title = a_tag.text.strip()
        # 僅保留 [賣/ 格式貼文，剔除 [徵/、[公告] 等
        if not re.search(r"\[\s*賣\s*/", title, re.IGNORECASE):
            continue

        href = a_tag.get("href", "")
        post_url = f"{PTT_BASE_URL}{href}" if href.startswith("/") else href

        # 從 href 抽取 post_id，例如 /bbs/HardwareSale/M.1793686654.A.CFC.html -> M.1793686654.A.CFC
        post_id_m = re.search(r"([A-Z]\.\d+\.[A-Z]\.[A-Z0-9]+)", href)
        post_id = post_id_m.group(1) if post_id_m else href

        meta_tag = rent.find("div", class_="meta")
        author = meta_tag.find("div", class_="author").text.strip() if meta_tag and meta_tag.find("div", class_="author") else "Anonymous"
        date_short = meta_tag.find("div", class_="date").text.strip() if meta_tag and meta_tag.find("div", class_="date") else ""

        posts.append({
            "post_id": post_id,
            "title": title,
            "post_url": post_url,
            "author": author,
            "date_short": date_short,
        })

    return posts


def fetch_ptt_post_details(post_url: str, session: requests.Session | None = None, timeout: int = 10) -> dict[str, Any]:
    """抓取單篇 PTT 文章詳細內文、發文時間與推文數。"""
    s = session or _get_ptt_session()
    resp = s.get(post_url, timeout=timeout)
    resp.raise_for_status()

    soup = bs4.BeautifulSoup(resp.text, "html.parser")
    main_content = soup.find("div", id="main-content")
    if not main_content:
        return {
            "content": "",
            "post_time": datetime.datetime.now().isoformat(),
            "comment_count": 0,
        }

    # 提取金屬欄位 (author, title, time)
    post_time_str = ""
    for meta in main_content.find_all("div", class_="article-metaline"):
        tag = meta.find("span", class_="article-meta-tag")
        val = meta.find("span", class_="article-meta-value")
        if tag and val and "時間" in tag.text:
            post_time_str = val.text.strip()

    # 計算推文數
    pushes = main_content.find_all("div", class_="push")
    comment_count = len(pushes)

    # 移除 metaline 與 push 獲取純內文
    for el in main_content.find_all(["div", "span"], class_=["article-metaline", "article-metaline-right", "push"]):
        el.decompose()

    clean_content = main_content.text.strip()
    post_time = parse_ptt_date(post_time_str) if post_time_str else datetime.datetime.now().isoformat()

    return {
        "content": clean_content,
        "post_time": post_time,
        "comment_count": comment_count,
    }


def _match_product_id(conn: sqlite3.Connection, spec_group: str) -> int | None:
    """嘗試從 products 資料表反查 product_id。"""
    cur = conn.execute(
        "SELECT product_id FROM products WHERE spec_group = ? LIMIT 1;",
        (spec_group,),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])

    # 寬鬆前綴匹配
    cur2 = conn.execute(
        "SELECT product_id FROM products WHERE spec_group LIKE ? LIMIT 1;",
        (f"%{spec_group}%",),
    )
    row2 = cur2.fetchone()
    return int(row2[0]) if row2 else None


def sync_ptt_to_db(
    conn: sqlite3.Connection,
    max_pages: int = 1,
    llm_parser_func: Callable[[str, str], dict[str, Any]] | None = None,
    sleep_range: tuple[float, float] = (1.2, 2.0),
) -> int:
    """抓取 PTT HardwareSale 二手貼文，解析並寫入 used_market_listings 資料庫。"""
    parser = llm_parser_func or parse_post_with_llm
    session = _get_ptt_session()
    current_url = HARDWARESALE_URL
    synced_count = 0

    for page_idx in range(max_pages):
        try:
            html, prev_url = fetch_ptt_page(current_url, session=session)
        except Exception:
            break

        posts = parse_ptt_post_list(html)

        for p in posts:
            post_id = p["post_id"]

            # 快取檢查：若已存在且已解析，跳過向網路或 LLM 重複請求
            cur = conn.execute("SELECT 1 FROM used_market_listings WHERE post_id = ?;", (post_id,))
            if cur.fetchone():
                continue

            # 抓取詳細文章內容
            try:
                details = fetch_ptt_post_details(p["post_url"], session=session)
            except Exception:
                continue

            # 結構化解析 (LLM 或 Fallback Regex)
            parsed = parser(p["title"], details["content"])

            # 販售狀態判定
            sale_status = "販售中"
            if re.search(r"(已售|售出|完售)", p["title"]):
                sale_status = "已售出"
            elif "洽中" in p["title"]:
                sale_status = "洽中"

            prod_id = _match_product_id(conn, parsed.get("spec_group", ""))

            listing_dict = {
                "post_id": post_id,
                "product_id": prod_id,
                "spec_group": parsed.get("spec_group") or "Unknown",
                "source_platform": "PTT HardwareSale",
                "seller_name": p["author"],
                "seller_type": parsed.get("seller_type", "個人自售"),
                "used_asking_price": parsed.get("used_asking_price", 0),
                "warranty_status": parsed.get("warranty_status", "過保"),
                "trade_method": parsed.get("trade_method", "面交 / 郵寄"),
                "comment_count": details["comment_count"],
                "sale_status": sale_status,
                "post_time": details["post_time"],
                "post_url": p["post_url"],
            }

            upsert_used_listing(conn, listing_dict)
            synced_count += 1

            # 禮貌延遲防 Ban
            if sleep_range[0] > 0:
                time.sleep(random.uniform(sleep_range[0], sleep_range[1]))

        if not prev_url:
            break
        current_url = prev_url
        if sleep_range[0] > 0:
            time.sleep(random.uniform(sleep_range[0], sleep_range[1]))

    return synced_count

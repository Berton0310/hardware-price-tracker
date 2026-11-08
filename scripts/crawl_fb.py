"""爬取指定 Facebook 二手社團貼文並同步入庫腳本 (scripts/crawl_fb.py)"""

from __future__ import annotations

from pathlib import Path
import sys

# 確保專案根目錄納入模組搜尋路徑
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 解決 Windows cp950 終端印出特殊 Unicode 字元之編碼問題
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from crawlers.fb_crawler import sync_fb_to_db
from database.db_manager import init_db

GROUPS = [
    ("社團 1: 二手筆電/主機/桌機/電腦/零件", "https://www.facebook.com/groups/2725975797677135/?locale=zh_TW"),
    ("社團 2: 二手電腦零件買賣交易區", "https://www.facebook.com/groups/1654207981498728/?locale=zh_TW"),
]

DB_PATH = "data/hardware.db"


def main(max_posts_per_group: int = 15):
    print("=" * 60)
    print("開始爬取 Facebook 二手電腦社團貼文...")
    print("=" * 60)

    conn = init_db(DB_PATH)
    total_synced = 0

    for idx, (name, url) in enumerate(GROUPS, 1):
        print(f"\n[{idx}/{len(GROUPS)}] 正在爬取 {name}...")
        print(f"網址: {url}")
        try:
            count = sync_fb_to_db(conn, group_url=url, max_posts=max_posts_per_group)
            total_synced += count
            print(f"[OK] {name} 成功同步入庫 {count} 則貼文！")
        except Exception as e:
            print(f"[FAIL] 爬取 {name} 發生異常: {e}")

    # 統計當前資料庫貼文總數
    cur = conn.execute("SELECT COUNT(*) FROM used_market_listings;")
    total_listings = cur.fetchone()[0]

    cur_fb = conn.execute(
        "SELECT COUNT(*) FROM used_market_listings WHERE source_platform LIKE '%Facebook%' OR source_platform LIKE '%FB%';"
    )
    fb_listings = cur_fb.fetchone()[0]

    conn.close()

    print("\n" + "=" * 60)
    print(f"爬取完成！本次新增/更新: {total_synced} 則貼文")
    print(f"目前資料庫二手貼文總數: {total_listings} 則（其中 FB 貼文: {fb_listings} 則）")
    print("=" * 60)


if __name__ == "__main__":
    max_p = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    main(max_p)

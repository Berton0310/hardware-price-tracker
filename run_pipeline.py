"""電腦硬體比價分析系統全流程執行腳本 (run_pipeline.py)

遵循 CLAUDE.md、AGENTS.md 與 Milestone 5 整合規範。
恪守「刪到不能再刪」極簡標準，提供一鍵爬蟲同步、終端機分析摘要與 Streamlit 儀表板啟動。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

# 解決 Windows 終端印出特殊 Unicode 字元之編碼問題
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 確保專案根目錄納入模組搜尋路徑
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from analysis.price_analysis import (
    analyze_new_price_trend,
    calculate_spec_group_discount,
    compare_platforms_and_sellers,
)
from crawlers.coolpc_crawler import sync_coolpc_to_db
from crawlers.ptt_crawler import sync_ptt_to_db
from database.db_manager import init_db

DEFAULT_DB_PATH = "data/hardware.db"


def sync_coolpc(db_path: str = DEFAULT_DB_PATH) -> int:
    """手動同步原價屋新品歷史報價。"""
    print(f"\n[1/2] 正在連線原價屋線上估價系統並同步最新報價 (DB: {db_path})...")
    conn = init_db(db_path)
    try:
        count = sync_coolpc_to_db(conn)
        print(f"  ✓ 成功同步 {count} 筆原價屋新品報價至資料庫。")
        return count
    except Exception as e:
        print(f"  ✗ 原價屋同步失敗: {e}")
        return 0
    finally:
        conn.close()


def sync_ptt(db_path: str = DEFAULT_DB_PATH, max_pages: int = 1) -> int:
    """手動同步 PTT HardwareSale 二手板最新刊登貼文。"""
    print(f"\n[2/2] 正在抓取 PTT HardwareSale 二手板貼文 (共 {max_pages} 頁, DB: {db_path})...")
    conn = init_db(db_path)
    try:
        count = sync_ptt_to_db(conn, max_pages=max_pages)
        print(f"  ✓ 成功同步 {count} 筆 PTT 二手交易貼文至資料庫。")
        return count
    except Exception as e:
        print(f"  ✗ PTT 貼文同步失敗: {e}")
        return 0
    finally:
        conn.close()


def sync_daily(db_path: str = DEFAULT_DB_PATH, ptt_pages: int = 1) -> dict[str, Any]:
    """執行每日全量資料同步排程（原價屋新品 + PTT 二手 + FB 社團優雅降級）。"""
    print(f"\n[*] 正在觸發每日全量資料同步流程 (DB: {db_path}, PTT 頁數: {ptt_pages})...")
    from scripts.daily_sync import run_daily_sync
    return run_daily_sync(db_path=db_path, ptt_pages=ptt_pages)


def print_analysis_report(db_path: str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """在終端機輸出當前硬體比價分析摘要報告。"""
    conn = init_db(db_path)
    try:
        # 查詢所有有記錄之規格群組
        cur = conn.execute(
            """
            SELECT DISTINCT spec_group, category FROM (
                SELECT spec_group, category FROM products WHERE spec_group IS NOT NULL AND TRIM(spec_group) != ''
                UNION
                SELECT u.spec_group, p.category
                FROM used_market_listings u
                LEFT JOIN products p ON u.product_id = p.product_id
                WHERE u.spec_group IS NOT NULL AND TRIM(u.spec_group) != ''
            ) ORDER BY category ASC, spec_group ASC;
            """
        )
        rows = cur.fetchall()
        spec_groups = [(r[0], r[1] or "OTHER") for r in rows if r[0]]

        if not spec_groups:
            print(f"\n[分析報告] 資料庫 ({db_path}) 目前為空，尚無任何規格產品或二手貼文。")
            return []

        print("\n" + "=" * 90)
        print(f"📊 電腦硬體新品與二手市場比價分析摘要報告 (DB: {db_path})")
        print("=" * 90)
        print(
            f"{'類別':<6} {'規格群組':<22} {'新品最低':<10} {'二手中位':<10} "
            f"{'折數':<8} {'轉二手省':<10} {'PTT/FB':<10} {'降價真偽':<10}"
        )
        print("-" * 90)

        summaries: list[dict[str, Any]] = []

        for spec, cat in spec_groups:
            discount_info = calculate_spec_group_discount(conn, spec)
            platform_info = compare_platforms_and_sellers(conn, spec)

            # 查詢代表性新品產品之歷史價格走勢與真假降價
            cur_p = conn.execute(
                """
                SELECT p.product_id FROM products p
                JOIN new_price_history h ON p.product_id = h.product_id
                WHERE p.spec_group = ?
                ORDER BY h.current_new_price ASC LIMIT 1;
                """,
                (spec,),
            )
            p_row = cur_p.fetchone()
            if p_row:
                trend = analyze_new_price_trend(conn, int(p_row[0]), days=90)
                authenticity = trend.get("discount_authenticity", "正常報價")
            else:
                authenticity = "無資料"

            new_min = discount_info.get("new_min_price")
            used_med = discount_info.get("used_median_price")
            diff = discount_info.get("saved_amount")
            ratio = discount_info.get("discount_ratio")
            disc_text = f"{ratio:.1f}折" if ratio is not None else "無折數"

            new_min_str = f"${new_min:,}" if new_min else "無報價"
            used_med_str = f"${int(round(used_med)):,}" if used_med else "無刊登"
            diff_str = f"+${int(round(diff)):,}" if diff and diff > 0 else ("$0" if diff == 0 else "無資料")

            groups = platform_info.get("groups", {})
            ptt_cnt = groups.get("PTT_individual", {}).get("count", 0) + groups.get("PTT_workshop", {}).get("count", 0)
            fb_cnt = groups.get("FB_Group_individual", {}).get("count", 0) + groups.get("FB_Group_workshop", {}).get("count", 0)
            plat_str = f"{ptt_cnt} / {fb_cnt}"

            print(
                f"{cat:<6} {spec:<22} {new_min_str:<10} {used_med_str:<10} "
                f"{disc_text:<8} {diff_str:<10} {plat_str:<10} {authenticity:<10}"
            )

            summaries.append({
                "category": cat,
                "spec_group": spec,
                "new_min_price": new_min,
                "used_median_price": used_med,
                "discount_ratio": ratio,
                "discount_text": disc_text,
                "saved_amount": diff,
                "discount_authenticity": authenticity,
                "ptt_count": ptt_cnt,
                "fb_count": fb_cnt,
            })

        print("=" * 90 + "\n")
        return summaries
    finally:
        conn.close()


def launch_dashboard(db_path: str = DEFAULT_DB_PATH) -> None:
    """啟動 Streamlit 互動視覺化儀表板。"""
    app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "main_app.py")
    env = os.environ.copy()
    env["HARDWARE_DB_PATH"] = db_path
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    print(f"\n🚀 正在啟動 Streamlit 儀表板 (DB: {db_path})...")
    print(f"   執行路徑: {app_path}")
    print("   即時熱重載: 已啟用 (--server.runOnSave=true，修改程式碼存檔即自動更新)")
    print("   瀏覽器將自動開啟 http://localhost:8501 (按 Ctrl+C 可停止伺服器)\n")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        app_path,
        "--server.runOnSave",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    subprocess.run(cmd, env=env)


def build_cli_parser() -> argparse.ArgumentParser:
    """建立命令列參數解析器。"""
    parser = argparse.ArgumentParser(
        description="電腦硬體新品歷史價格與二手市場行情比價分析系統 - 全流程運行腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sync-coolpc",
        action="store_true",
        help="手動同步原價屋新品線上估價歷史報價",
    )
    parser.add_argument(
        "--sync-ptt",
        action="store_true",
        help="手動同步 PTT HardwareSale 二手板最新刊登貼文",
    )
    parser.add_argument(
        "--sync-daily",
        action="store_true",
        help="觸發每日全量資料同步流程（整合原價屋新品 + PTT 二手 + FB 社團優雅降級）",
    )
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="在終端機輸出當前硬體比價分析摘要報告",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="啟動 Streamlit 互動視覺化儀表板",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="一鍵執行全流程（原價屋同步 -> PTT 同步 -> 分析摘要 -> 啟動 App）",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"指定 SQLite 資料庫檔案路徑 (預設: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--ptt-pages",
        type=int,
        default=1,
        help="指定 PTT 爬取頁數 (預設: 1 頁)",
    )
    return parser


def main() -> None:
    """CLI 主程式進入點。"""
    parser = build_cli_parser()
    args = parser.parse_args()

    # 若未提供任何指令旗標，印出說明並執行分析摘要
    if not (args.sync_coolpc or args.sync_ptt or args.sync_daily or args.analysis or args.app or args.all):
        parser.print_help()
        print("\n提示: 未指定具體旗標，將為您展示當前分析摘要報告：")
        print_analysis_report(args.db)
        return

    if args.all:
        sync_coolpc(args.db)
        sync_ptt(args.db, max_pages=args.ptt_pages)
        print_analysis_report(args.db)
        launch_dashboard(args.db)
        return

    if args.sync_daily:
        sync_daily(args.db, ptt_pages=args.ptt_pages)

    if args.sync_coolpc:
        sync_coolpc(args.db)

    if args.sync_ptt:
        sync_ptt(args.db, max_pages=args.ptt_pages)

    if args.analysis:
        print_analysis_report(args.db)

    if args.app:
        launch_dashboard(args.db)


if __name__ == "__main__":
    main()

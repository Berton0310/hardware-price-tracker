"""Facebook 互動式登入與 Cookie 導出小工具 (login_fb.py)

遵循「刪到不能再刪」極簡標準。
使用 Playwright 開啟有頭瀏覽器，由使用者自行登入 Facebook（支援 2FA 雙重驗證），
並將登入後之 Session Cookies 導出儲存至 data/fb_cookies.json。
"""

from __future__ import annotations

import json
import os
import sys

from playwright.sync_api import sync_playwright

COOKIE_FILE_PATH = "data/fb_cookies.json"
FB_LOGIN_URL = "https://www.facebook.com"


def interactive_login(output_path: str = COOKIE_FILE_PATH) -> bool:
    """啟動可視化瀏覽器供使用者手動登入 FB，並儲存 Cookie。"""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    print("=" * 60)
    print("【Facebook 互動登入工具】")
    print("即將開啟 Chromium 瀏覽器，請在視窗中登入您的 Facebook 帳號。")
    print("支援 2FA 簡訊/驗證器驗證。登入完成並抵達首頁後，請回到此終端機按 Enter。")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            page.goto(FB_LOGIN_URL, timeout=60000)
            input("\n>>> 當您登入完成並看到 Facebook 動態首頁後，請在此按下 [Enter] 鍵繼續：")

            cookies = context.cookies()
            # 檢查是否有關鍵登入 Cookie（c_user 或 xs）
            has_auth = any(c.get("name") in ("c_user", "xs") for c in cookies)
            if not has_auth:
                print("【警告】未檢測到 Facebook 登入憑證 (c_user/xs)，可能尚未成功登入。")
                confirm = input("是否仍要強制儲存目前 Cookie？ (y/n): ").strip().lower()
                if confirm != "y":
                    print("已取消儲存。")
                    browser.close()
                    return False

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)

            print(f"【成功】Cookie 已成功導出至：{os.path.abspath(output_path)}")
            browser.close()
            return True
        except Exception as e:
            print(f"【錯誤】登入導出過程發生異常：{e}")
            browser.close()
            return False


if __name__ == "__main__":
    success = interactive_login()
    sys.exit(0 if success else 1)

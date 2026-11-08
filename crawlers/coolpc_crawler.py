"""原價屋新品估價單爬蟲模組 (crawlers/coolpc_crawler.py)

遵循「刪到不能再刪」極簡標準。
負責抓取並解析原價屋 evaluate.php 靜態 HTML（Big5/cp950 編碼），
涵蓋 GPU(12), RAM(6), SSD(7), CPU(4) 四大核心類別，並同步至 SQLite 資料庫。
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Any

import bs4
import requests

from database.db_manager import record_new_price, upsert_product

COOLPC_EVALUATE_URL = "https://www.coolpc.com.tw/evaluate.php"

# 4 大核心類別映射：(select_name, category_name)
CATEGORY_MAPPING = {
    "n12": "GPU",
    "n6": "RAM",
    "n7": "SSD",
    "n4": "CPU",
}

BRAND_MAP = {
    "華碩": "ASUS",
    "asus": "ASUS",
    "微星": "MSI",
    "msi": "MSI",
    "技嘉": "Gigabyte",
    "gigabyte": "Gigabyte",
    "aorus": "Gigabyte",
    "艾維克": "EVGA",
    "evga": "EVGA",
    "索泰": "ZOTAC",
    "zotac": "ZOTAC",
    "映眾": "Inno3D",
    "inno3d": "Inno3D",
    "華擎": "ASRock",
    "asrock": "ASRock",
    "英特爾": "Intel",
    "intel": "Intel",
    "超微": "AMD",
    "amd": "AMD",
    "金士頓": "Kingston",
    "kingston": "Kingston",
    "美光": "Crucial",
    "crucial": "Crucial",
    "鎧俠": "Kioxia",
    "kioxia": "Kioxia",
    "威剛": "ADATA",
    "adata": "ADATA",
    "十銓": "TeamGroup",
    "teamgroup": "TeamGroup",
    "t-force": "TeamGroup",
    "芝奇": "G.Skill",
    "g.skill": "G.Skill",
    "科賦": "Klevv",
    "klevv": "Klevv",
    "三星": "Samsung",
    "samsung": "Samsung",
    "海盜船": "Corsair",
    "corsair": "Corsair",
    "酷碼": "CoolerMaster",
    "coolermaster": "CoolerMaster",
}


def _extract_brand_from_text(text: str) -> str:
    """從型號文字中抽取標準化英文廠牌。"""
    lower = text.lower()
    for kw, std in BRAND_MAP.items():
        if kw.isascii():
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                return std
        else:
            if kw in lower:
                return std
    return "Generic"


def _extract_prices_and_change(text: str) -> tuple[int, int, str]:
    """抽取原價、現價與漲跌標籤。支援雙價格 ($10500↘$9490 / $9000↗$9500) 與單價格 ($9990 / ↘$9490)。"""
    m_double = re.search(r"\$?(\d+)\s*([↘↗])\s*\$?(\d+)", text)
    if m_double:
        p1 = int(m_double.group(1))
        arrow = m_double.group(2)
        p2 = int(m_double.group(3))
        if arrow == "↘":
            listed = max(p1, p2)
            current = min(p1, p2)
            tag = "↘降價"
        else:
            listed = p1
            current = p2
            tag = "↗漲價"
        return listed, current, tag

    after_comma = text.split(",")[-1] if "," in text else text
    m_single = re.search(r"([↘↗])?\s*\$?(\d+)", after_comma)
    if m_single:
        arrow = m_single.group(1)
        price = int(m_single.group(2))
        if arrow == "↘":
            tag = "↘降價"
        elif arrow == "↗":
            tag = "↗漲價"
        else:
            tag = "不變"
        return price, price, tag

    return 0, 0, "不變"


def _clean_spec_group(category: str, optgroup_label: str, option_text: str) -> str:
    """標準化規格群組 (spec_group)。"""
    label = optgroup_label.strip()
    # 去除廠商前綴與多餘註釋
    clean_og = re.sub(r"^(?:NVIDIA|AMD|INTEL)\s+(?:Radeon\s+)?", "", label, flags=re.IGNORECASE)
    clean_og = re.sub(r"\((?:GDDR\d+|不含散熱器)\)", "", clean_og).strip()
    clean_og = re.sub(r"【.*?】|『.*?』", "", clean_og).strip()

    if category == "GPU":
        # 例如 NVIDIA RTX4060-8GB -> RTX 4060 8G, NVIDIA RTX4060Ti-16GB -> RTX 4060 Ti 16G
        norm = re.sub(r"\b(RTX|GTX)(\d+)", r"\1 \2", clean_og, flags=re.IGNORECASE)
        norm = re.sub(r"(\d+)(Ti|SUPER)", r"\1 \2", norm, flags=re.IGNORECASE)
        norm = re.sub(r"[- ](\d+)GB?\b", r" \1G", norm, flags=re.IGNORECASE)
        m = re.search(
            r"((?:RTX|GTX)\s*\d{3,4}(?:\s*Ti|\s*SUPER)?(?:\s*\d+G)?|RX\s*\d{4}(?:\s*XT(?:X)?)?(?:\s*\d+G)?)",
            norm,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            val = re.sub(r"\bTI\b", "Ti", val, flags=re.IGNORECASE)
            val = re.sub(r"\bSUPER\b", "SUPER", val, flags=re.IGNORECASE)
            return val
        return clean_og or "Other-GPU"

    elif category == "RAM":
        # 提取頻率 (如 DDR5-6000) 與容量 (如 32G)
        m_speed = re.search(r"(DDR[345][-\s]?\d{4})", option_text, re.IGNORECASE)
        m_cap = re.search(r"\b(\d+)\s*GB?\b", option_text, re.IGNORECASE)
        if m_speed and m_cap:
            speed = re.sub(r"\s+", "-", m_speed.group(1).upper())
            cap = f"{m_cap.group(1)}G"
            return f"{speed} {cap}"
        elif m_speed:
            return re.sub(r"\s+", "-", m_speed.group(1).upper())
        return clean_og or "Other-RAM"

    elif category == "SSD":
        # 嘗試從 option 內提取如 Gen4 1TB / Gen5 2TB / 1TB / 2TB
        m = re.search(r"((?:Gen[345]|PCIe\s*[345])\s*\d+[TG]B?|\d+[TG]B\s*SSD|\d+[TG]B)", option_text, re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1).upper()).strip()
        return clean_og or "Other-SSD"

    elif category == "CPU":
        # 從 option 內提取主要 CPU 型號，如 i5-14400F、i7-14700K、R7 7800X3D
        m = re.search(r"(Ryzen\s*\d\s*\d{4}[A-Z0-9]*|R[3579]\s*\d{4}[A-Z0-9]*|i[3579]-\d{4,5}[A-Z0-9]*|Ultra\s*\d\s*\d{3}[A-Z0-9]*)", option_text, re.IGNORECASE)
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            val = re.sub(r"^I([3579]-)", r"i\1", val, flags=re.IGNORECASE)
            return val
        return clean_og or "Other-CPU"

    return clean_og or "Unknown"


def _extract_warranty_years(text: str) -> int:
    """從產品文字中推斷原廠保固年限。"""
    if "五年保" in text or "註冊五年保" in text or "註冊5年保" in text:
        return 5
    if "四年保" in text or "註四年" in text or "註冊4年保" in text:
        return 4
    if "三年保" in text or "註冊三年保" in text or "註冊3年保" in text:
        return 3
    if "兩年保" in text or "2年保" in text or "二年保" in text:
        return 2
    if "終身保" in text or "終保" in text:
        return 10
    return 3


def fetch_coolpc_html(timeout: int = 15) -> str:
    """抓取原價屋 evaluate.php 靜態 HTML，並依 cp950 編碼解碼。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    resp = requests.get(COOLPC_EVALUATE_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()
    # 原價屋使用 cp950 / Big5 編碼
    return resp.content.decode("cp950", errors="replace")


def parse_coolpc_html(html: str) -> list[dict[str, Any]]:
    """解析原價屋 evaluate.php 之四大核心類別（GPU, RAM, SSD, CPU）。"""
    soup = bs4.BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []

    for select_name, category in CATEGORY_MAPPING.items():
        select_tag = soup.find("select", {"name": select_name})
        if not select_tag:
            continue

        for og in select_tag.find_all("optgroup"):
            og_label = og.get("label", "").strip()
            # 排除純周邊配件
            if "周邊配件" in og_label:
                continue

            for opt in og.find_all("option"):
                text = opt.text.strip()
                val = opt.get("value", "")

                # 排除匯總行或無價格選項
                if val == "0" or "$" not in text:
                    continue
                if text.startswith("❤") or text.startswith("共有商品"):
                    continue

                # 價格與漲跌提取：支援 $10000 / $12000↘$10000 / $10000↗$12000 / ↘$10000
                listed_price, current_price, change_tag = _extract_prices_and_change(text)
                if current_price <= 0:
                    continue

                # GPU 類別排除非顯卡配件 (如 mini-DP 轉接線、顯卡支架等，且最低價顯卡至少 $500 以上)
                if category == "GPU":
                    if re.search(r"(轉接線|轉接頭|轉接器|轉接|支架|排線|延長線|散熱片|散熱貼)", text, re.IGNORECASE):
                        continue
                    if current_price < 500:
                        continue

                # 型號與規格文字清洗
                raw_model_part = re.sub(r",\s*[$↘↗].*$", "", text).strip()
                raw_model_part = re.sub(r"^[❤◆★\s]+", "", raw_model_part).strip()

                # 去除｛ ｝符號但保留適當空格
                clean_model_name = (
                    raw_model_part.replace("｛", "")
                    .replace("｝", " ")
                    .replace("{", "")
                    .replace("}", " ")
                    .strip()
                )
                clean_model_name = re.sub(r"\s+", " ", clean_model_name)

                brand = _extract_brand_from_text(raw_model_part)
                spec_group = _clean_spec_group(category, og_label, raw_model_part)
                warranty_years = _extract_warranty_years(raw_model_part)

                items.append({
                    "category": category,
                    "spec_group": spec_group,
                    "brand": brand,
                    "model_name": clean_model_name,
                    "key_specs": raw_model_part,
                    "warranty_years": warranty_years,
                    "listed_price": listed_price,
                    "current_price": current_price,
                    "change_tag": change_tag,
                })

    return items


def sync_coolpc_to_db(conn: sqlite3.Connection, html: str | None = None) -> int:
    """抓取或解析原價屋 HTML 並寫入 products 與 new_price_history 資料表。"""
    if html is None:
        html = fetch_coolpc_html()

    items = parse_coolpc_html(html)
    today_str = datetime.date.today().isoformat()
    count = 0

    for it in items:
        prod_id = upsert_product(
            conn=conn,
            category=it["category"],
            spec_group=it["spec_group"],
            brand=it["brand"],
            model_name=it["model_name"],
            key_specs=it.get("key_specs", ""),
            warranty_years=it.get("warranty_years", 3),
        )
        record_new_price(
            conn=conn,
            product_id=prod_id,
            record_date=today_str,
            listed_price=it["listed_price"],
            current_price=it["current_price"],
            change_tag=it["change_tag"],
            in_stock=True,
        )
        count += 1

    return count

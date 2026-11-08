"""OpenRouter LLM 自然語言與正則備援二手貼文結構化解析模組 (cleaners/llm_parser.py)

遵循「刪到不能再刪」極簡標準。
支援 OpenRouter API 結構化解析與無金鑰/離線時的正則 Fallback 機制。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"

SYSTEM_PROMPT = """你是一個專業的電腦硬體二手交易貼文解析助理。
請從使用者提供的二手貼文標題與內文中抽取硬體規格資訊，並「僅」輸出合法的 JSON 物件，格式如下：
{
  "category": "GPU / CPU / RAM / SSD / OTHER",
  "spec_group": "晶片組或規格群組（如 RTX 4060 Ti 16G、R7 7800X3D、DDR5-6000 32G）",
  "brand": "英文廠牌（如 ASUS, MSI, Gigabyte, Intel, AMD, Kingston 等）",
  "model_name": "具體產品型號",
  "used_asking_price": 數字整數金額（如 10500，無幣值符號），若有多個硬體請取主硬體欲售金額，找不到則填 0,
  "warranty_status": "原廠保內 / 過保 / 終保 / 無保",
  "seller_type": "個人自售 或 工作室/礦老闆（若有大量、多卡、多張、礦卡等特徵）",
  "trade_method": "交易方式（如 面交 / 賣貨便 / 郵寄）",
  "condition_notes": "品況簡述"
}
注意：請不要添加額外的 Markdown 區塊或解釋，直接輸出單一合法 JSON 字串。
"""

BRAND_MAP = {
    "華碩": "ASUS",
    "asus": "ASUS",
    "微星": "MSI",
    "msi": "MSI",
    "技嘉": "Gigabyte",
    "gigabyte": "Gigabyte",
    "aorus": "Gigabyte",
    "鷹廠": "Gigabyte",
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
    "三星": "Samsung",
    "samsung": "Samsung",
    "宏碁": "Acer",
    "acer": "Acer",
    "十銓": "TeamGroup",
    "teamgroup": "TeamGroup",
    "t-force": "TeamGroup",
    "威剛": "ADATA",
    "adata": "ADATA",
    "科賦": "Klevv",
    "klevv": "Klevv",
    "海盜船": "Corsair",
    "corsair": "Corsair",
    "芝奇": "G.Skill",
    "g.skill": "G.Skill",
    "gskill": "G.Skill",
}


EXCLUDE_WORDS = {
    "賣", "徵", "售", "換", "出清", "求", "贈", "排", "合售", "合買", "合購",
    "facebook", "fb", "ptt", "二手", "全新", "盒裝", "完整", "正常", "故障", "零件",
    "面交", "郵寄", "店到店", "全省", "全國", "雙北", "台北", "新北", "桃園", "新竹",
    "苗栗", "台中", "彰化", "南投", "雲林", "嘉義", "台南", "高雄", "屏東", "宜蘭",
    "花蓮", "台東", "澎湖", "金門", "馬祖", "皆可", "賣貨便", "好賣+", "蝦皮", "黑貓",
    "宅配", "自取", "捷運", "可議", "不議", "暫定", "已售", "洽中", "排隊",
}


def _clean_title_text(text: str) -> str:
    """清除標題中的 PTT/FB 標籤、括號、地區與交易冗餘字。"""
    cleaned = re.sub(r"\[[^\]]*\]", " ", text)
    cleaned = re.sub(r"【[^】]*】|『[^』]*』|（[^）]*）|\([^\)]*\)", " ", cleaned)
    cleaned = re.sub(r"\b(Facebook|FB)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s\-\.\+]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_brand(text: str) -> str:
    """從文字中萃取並標準化廠牌名稱。"""
    lower_text = text.lower()
    for kw, std in BRAND_MAP.items():
        if re.search(rf"\b{re.escape(kw)}\b" if kw.isascii() else re.escape(kw), lower_text):
            return std
    return "Generic"


def _extract_category(text: str) -> str:
    """從文字推斷硬體分類。優先辨識明確特徵，防範內文故事干擾。"""
    if re.search(r"\b(rtx|gtx|rx\s*\d{3,4}|gpu|radeon|geforce)\b|顯卡|顯示卡", text, re.IGNORECASE):
        return "GPU"
    if re.search(r"\b(ddr[345]|ram)\b|記憶體|\b\d+gb?\s*(?:x\s*2|\*\s*2)\b", text, re.IGNORECASE):
        return "RAM"
    if re.search(r"\b(ssd|nvme|m\.2|pcie\s*[345]|gen[345]|sata)\b|固態硬碟", text, re.IGNORECASE):
        return "SSD"
    if re.search(r"\b(ryzen|ultra\s*\d|core\s*i[3579]|cpu|x3d)\b|\b(?:i[3579]|r[3579])[- ]?\d{4,5}[a-z]*|處理器", text, re.IGNORECASE):
        return "CPU"
    return "OTHER"


def _extract_spec_group(category: str, text: str) -> str:
    """根據類別提取核心晶片組或規格群組。"""
    if category == "GPU":
        norm = re.sub(r"\b(RTX|GTX)(\d+)", r"\1 \2", text, flags=re.IGNORECASE)
        norm = re.sub(r"(\d+)(Ti|SUPER)", r"\1 \2", norm, flags=re.IGNORECASE)
        norm = re.sub(r"[- ](\d+)GB?\b", r" \1G", norm, flags=re.IGNORECASE)
        m = re.search(
            r"((?:rtx|gtx)\s*\d{3,4}(?:\s*ti|\s*super)?(?:\s*\d+g)?|rx\s*\d{4}(?:\s*xt(?:x)?)?(?:\s*\d+g)?)",
            norm,
            re.IGNORECASE,
        )
        if m:
            clean = re.sub(r"\s+", " ", m.group(1)).strip()
            clean = re.sub(r"\b(rtx|gtx)\b", lambda m: m.group(0).upper(), clean, flags=re.IGNORECASE)
            clean = re.sub(r"\bti\b", "Ti", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\bsuper\b", "SUPER", clean, flags=re.IGNORECASE)
            return clean

    elif category == "CPU":
        m = re.search(
            r"(ryzen\s*\d?\s*\d{4}(?:x3d|x|g|f)?|r[3579]\s*\d{4}(?:x3d|x|g|f)?|i[3579]-\d{4,5}[a-z]*|ultra\s*\d\s*\d{3}[a-z]*)",
            text,
            re.IGNORECASE,
        )
        if m:
            clean = re.sub(r"\s+", " ", m.group(1)).strip()
            clean = re.sub(r"^I([3579]-)", r"i\1", clean, flags=re.IGNORECASE)
            return clean

    elif category == "RAM":
        m_speed = re.search(r"(DDR[345][-\s]?\d{4})", text, re.IGNORECASE)
        # 嚴格限制記憶體容量在合理範圍 (4~256G)，嚴防把 512GB 等 SSD 容量誤判為 RAM
        m_kit = re.search(r"\b(4|8|16|24|32|48|64)\s*GB?\s*[*xX]\s*2\b", text, re.IGNORECASE)
        m_cap = re.search(r"\b(4|8|16|24|32|48|64|96|128|256)\s*GB?\b", text, re.IGNORECASE)
        if m_kit:
            cap_str = f"{int(m_kit.group(1)) * 2}G"
        elif m_cap:
            cap_str = f"{m_cap.group(1)}G"
        else:
            cap_str = ""

        if m_speed and cap_str:
            speed = re.sub(r"\s+", "-", m_speed.group(1).upper())
            return f"{speed} {cap_str}"
        elif m_speed:
            return re.sub(r"\s+", "-", m_speed.group(1).upper())

    elif category == "SSD":
        m = re.search(r"((?:gen[345]|pcie[345])\s*\d+[tg]b?|\d+[tg]b\s*ssd|\d+[tg]b)", text, re.IGNORECASE)
        if m:
            clean = re.sub(r"\s+", " ", m.group(1).upper())
            return clean

    # Fallback: 使用清洗後的關鍵字，過濾掉交易關鍵字、地名與方括號，杜絕 '[賣' 與 'Facebook'
    clean_words = []
    for w in re.split(r"[\s\-_/]+", text):
        clean_w = re.sub(r"^[\[\(【『]+|[\]\)】』]+$", "", w).strip()
        if len(clean_w) >= 2 and clean_w.lower() not in EXCLUDE_WORDS:
            clean_words.append(clean_w)
    return clean_words[0] if clean_words else "Unknown"


def fallback_regex_parse(title: str, content: str) -> dict[str, Any]:
    """純正規表達式備援解析器，100% 離線穩定運作。"""
    combined = f"{title}\n{content}"

    # 1. 抽取價格：過濾防呆假標價 (1, 99999, 88888)，支援萬字、合售、合買、序號式售價
    price = 0
    DUMMY_PRICES = {1, 99999, 999999, 88888, 77777, 123456}

    # 1.1 萬字轉換 (如: 5.3萬 -> 53000, 2萬 -> 20000)
    m_wan = re.search(r"(?:欲售價格|價格|售價|售|賣)[^0-9\n\r]{0,15}?(\d+(?:\.\d+)?)\s*[萬Ww]", combined)
    if m_wan:
        price = int(float(m_wan.group(1)) * 10000)

    # 1.2 優先尋找「實售」、「實際欲售」、「底價」等精確修飾語
    if price == 0:
        actual_matches = re.findall(
            r"(?:實際欲售|實售|實際售價|實際價格|底價|實際)\s*[：:]?\s*(?:nt\$?|\$)?\s*([0-9,]{3,7})",
            combined,
            re.IGNORECASE,
        )
        for p in actual_matches:
            val_str = p.replace(",", "").strip()
            if val_str.isdigit():
                val = int(val_str)
                if val not in DUMMY_PRICES and val > 50:
                    price = val
                    break

    # 1.3 截取「◎欲售價格」區塊（至下一個「◎」或換行兩次），解析合售、序號式與區塊價格
    if price == 0:
        m_block = re.search(r"◎欲售價格[：:]?\s*([\s\S]*?)(?=◎|\n\s*\n|$)", content)
        price_block = m_block.group(1).strip() if m_block else ""
        if price_block:
            # 合售 / 合買 / 合購 / 打包
            m_bundle = re.search(
                r"(?:合售|合買|合購|打包|全收|一併)\s*[：:]?\s*(?:nt\$?|\$)?\s*([0-9,]{3,7})",
                price_block,
                re.IGNORECASE,
            )
            if m_bundle:
                val = int(m_bundle.group(1).replace(",", ""))
                if val not in DUMMY_PRICES and val > 50:
                    price = val

            # 序號式：1. 8000, 1.700, 1=3000, 1+2=3000, 1: 3000, 1~5合售 8000
            if price == 0:
                m_num = re.search(
                    r"(?:^\s*|\n\s*|\b)(?:\d+[\+\d]*\s*[\.=:：]\s*|\d+~\d+[\u4e00-\u9fa5]*\s*)(?:nt\$?|\$)?\s*([0-9,]{3,7})",
                    price_block,
                    re.IGNORECASE,
                )
                if m_num:
                    val = int(m_num.group(1).replace(",", ""))
                    if val not in DUMMY_PRICES and val > 50:
                        price = val

            # 區塊內所有金額依序嘗試
            if price == 0:
                all_nums = re.findall(r"(?:nt\$?|\$)?\s*([0-9,]{3,7})\s*(?:元|\$)?", price_block, re.IGNORECASE)
                for num_str in all_nums:
                    clean_str = num_str.replace(",", "").strip()
                    if clean_str.isdigit():
                        val = int(clean_str)
                        if val not in DUMMY_PRICES and val > 50:
                            price = val
                            break

    # 1.4 若無實售修飾語，尋找常見價格標籤 (相容 FB「售 18000」、「賣 18000」等無冒號寫法)
    if price == 0:
        price_matches = re.findall(
            r"(?:◎欲售價格|欲售價格|價格|售價|賣|售|\$|nt\$?)\s*[：:]?\s*(?:[0-9]{1,2}[\.=:：]\s*)?(?:nt\$?|\$)?\s*([0-9,]{3,7})",
            combined,
            re.IGNORECASE,
        )
        for p in price_matches:
            val_str = p.replace(",", "").strip()
            if val_str.isdigit():
                val = int(val_str)
                if val not in DUMMY_PRICES and val > 50:
                    price = val
                    break

    # 1.5 若仍為 0，嘗試匹配「xxxx 元」
    if price == 0:
        alt_matches = re.findall(r"([1-9]\d{2,6})\s*元", combined)
        for alt_str in alt_matches:
            val = int(alt_str)
            if val not in DUMMY_PRICES and val > 50:
                price = val
                break

    # 2. 類別、廠牌、規格群組 (優先從硬體型號與標題提取，防範內文故事干擾)
    model_m = re.search(r"◎硬體型號[：:]\s*([^\n\r]+)", content)
    hardware_text = model_m.group(1).strip() if model_m else ""

    clean_title = _clean_title_text(title)
    target_spec_text = f"{clean_title} {hardware_text}".strip()
    if not target_spec_text:
        target_spec_text = f"{clean_title} {content[:200]}"

    category = _extract_category(target_spec_text)
    brand = _extract_brand(target_spec_text)
    spec_group = _extract_spec_group(category, target_spec_text)

    # 3. 型號名稱
    if model_m:
        model_name = model_m.group(1).strip()
    else:
        model_name = clean_title or spec_group

    # 4. 保固狀態：優先辨識終身保固（終保）
    if re.search(r"(終身保固|終身保|終保)", combined):
        warranty_status = "終保"
    elif re.search(r"(保固內|保內|原廠保|保固至|註冊[四五]年保|保固中)", combined):
        warranty_status = "原廠保內"
    elif re.search(r"(過保|無保|過期)", combined):
        warranty_status = "過保"
    else:
        warranty_status = "過保"

    # 5. 賣家類型（防範大量拋售/礦老闆）
    if re.search(r"(多卡|多張|大量|礦卡|礦老闆|工作室|礦場|多組|一次全收)", combined):
        seller_type = "工作室/礦老闆"
    else:
        seller_type = "個人自售"

    # 6. 交易方式
    trade_m = re.search(r"◎交易方式[：:]\s*([^\n\r]+)", content)
    if trade_m:
        trade_method = trade_m.group(1).strip()
    elif re.search(r"(面交)", combined) and re.search(r"(賣貨便|好賣\+|郵寄|店到店)", combined):
        trade_method = "面交 / 店到店"
    elif re.search(r"(面交)", combined):
        trade_method = "面交"
    elif re.search(r"(賣貨便|好賣\+|店到店)", combined):
        trade_method = "賣貨便 / 店到店"
    else:
        trade_method = "面交 / 郵寄"

    # 7. 品況簡述
    cond_m = re.search(r"◎品樣狀況[：:]\s*([^\n\r]+)", content)
    condition_notes = cond_m.group(1).strip() if cond_m else "二手正常使用"

    return {
        "category": category,
        "spec_group": spec_group,
        "brand": brand,
        "model_name": model_name,
        "used_asking_price": price,
        "warranty_status": warranty_status,
        "seller_type": seller_type,
        "trade_method": trade_method,
        "condition_notes": condition_notes,
    }


def parse_post_with_llm(
    title: str,
    content: str,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """使用 OpenRouter LLM 解析二手貼文；若無金鑰或連線失敗，自動降級為正則解析。"""
    key = api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return fallback_regex_parse(title, content)

    chosen_model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/hardware-crawler",
        "X-Title": "Hardware Price Analyzer",
    }
    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"標題: {title}\n內文:\n{content[:2000]}"},
        ],
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return fallback_regex_parse(title, content)

        data = resp.json()
        raw_content = data["choices"][0]["message"]["content"].strip()
        # 清除可能的 markdown code fence
        if raw_content.startswith("```"):
            raw_content = re.sub(r"^```(?:json)?", "", raw_content)
            raw_content = re.sub(r"```$", "", raw_content).strip()

        parsed = json.loads(raw_content)

        # 欄位完整性補齊
        fb = fallback_regex_parse(title, content)
        return {
            "category": str(parsed.get("category") or fb["category"]),
            "spec_group": str(parsed.get("spec_group") or fb["spec_group"]),
            "brand": str(parsed.get("brand") or fb["brand"]),
            "model_name": str(parsed.get("model_name") or fb["model_name"]),
            "used_asking_price": int(parsed.get("used_asking_price") or fb["used_asking_price"]),
            "warranty_status": str(parsed.get("warranty_status") or fb["warranty_status"]),
            "seller_type": str(parsed.get("seller_type") or fb["seller_type"]),
            "trade_method": str(parsed.get("trade_method") or fb["trade_method"]),
            "condition_notes": str(parsed.get("condition_notes") or fb["condition_notes"]),
        }
    except Exception:
        return fallback_regex_parse(title, content)

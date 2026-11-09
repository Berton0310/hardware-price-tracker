"""二手貼文 LLM 與正則備援解析器單元測試 (tests/test_llm_parser.py)

驗證測試重點：
1. fallback_regex_parse 極端與混亂真實二手貼文解析：
   - 價格抽取：精準抽取欲售價格，剔除購買原價、假標價 ($1, $99999 等)。
   - 品牌與晶片規格抽取：GPU (RTX 4060Ti), RAM (DDR5), SSD, CPU。
   - 保固狀態：原廠保內 / 過保 / 終保。
   - 賣家分類：個人自售 vs 商業工作室/礦老闆。
2. parse_post_with_llm 降級機制：
   - 無 API Key 時自動降級為正則解析，不拋異常。
   - API 網路異常、超時、非 200 回應時自動降級。
   - API 回傳非 JSON 格式時安全容錯降級。
   - 正常回傳有效 JSON 時之標準化抽取。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from cleaners.llm_parser import fallback_regex_parse, parse_post_with_llm


class TestFallbackRegexParse:
    """測試 fallback_regex_parse 在無 LLM 時的純正則抽取強健性。"""

    def test_gpu_post_with_purchase_price_and_warranty(self):
        """測試 GPU 貼文：包含購買原價、原廠保內、個人自售，需精準抽取欲售價格而非購買原價。"""
        title = "[賣/台北/面交] 技嘉 RTX 4060 Ti Eagle 8G 盒裝完整"
        content = """
        ◎硬體型號：技嘉 RTX 4060 Ti Eagle 8G
        ◎欲售價格：10,500
        ◎品樣狀況：2023年7月原價屋購入，購買價格 15,990 元，原廠保固內至 2028 年
        ◎佐證資料：附發票照片
        ◎交易方式：捷運站面交、賣貨便
        ◎交易地區：雙北
        ◎聯絡方式：站內信
        """
        res = fallback_regex_parse(title, content)
        assert res["category"] == "GPU"
        assert "4060" in res["spec_group"]
        assert res["brand"] == "Gigabyte"
        assert res["used_asking_price"] == 10500  # 必須是欲售價格 10500，不能是購買價格 15990
        assert res["warranty_status"] == "原廠保內"
        assert res["seller_type"] == "個人自售"
        assert "面交" in res["trade_method"]

    def test_gpu_post_dummy_placeholder_price(self):
        """測試假標價 ($99999 或 $1 防呆)，內文包含實際欲售金額。"""
        title = "[賣/高雄/面交] 微星 RTX 4060 Ti VENTUS 8G 標價99999防呆 實售9800"
        content = """
        ◎硬體型號：MSI RTX 4060 Ti VENTUS 2X 8G
        ◎欲售價格：99999 (防呆勿下標，實際欲售 9800 元)
        ◎品樣狀況：2023/10 購買，原廠保固中
        ◎交易方式：面交
        """
        res = fallback_regex_parse(title, content)
        assert res["category"] == "GPU"
        assert res["brand"] == "MSI"
        assert res["used_asking_price"] == 9800  # 剔除 99999 假標價，取得實售 9800
        assert res["warranty_status"] == "原廠保內"

    def test_ram_post_lifetime_warranty(self):
        """測試 RAM 貼文：終身保固、DDR5 規格群組。"""
        title = "[賣/全國/店到店] 美光 Crucial PRO DDR5-6000 32G (16G*2)"
        content = """
        ◎硬體型號：美光 Crucial PRO DDR5-6000 32G (16G*2)
        ◎欲售價格：2,800
        ◎品樣狀況：捷元代理終身保固，盒裝完整功能正常
        ◎交易方式：7-11 賣貨便
        """
        res = fallback_regex_parse(title, content)
        assert res["category"] == "RAM"
        assert "DDR5" in res["spec_group"]
        assert res["brand"] == "Crucial"
        assert res["used_asking_price"] == 2800
        assert res["warranty_status"] == "終保"
        assert res["seller_type"] == "個人自售"

    def test_ssd_post_out_of_warranty(self):
        """測試 SSD 貼文：過保、SSD 規格。"""
        title = "[賣/新北/面交] 三星 980 PRO 1TB SSD"
        content = """
        ◎硬體型號：Samsung 980 PRO 1TB PCIe 4.0 NVMe M.2 SSD
        ◎欲售價格：1800
        ◎品樣狀況：讀寫量約 40TB，已過保固，個人保固三天
        ◎交易方式：新莊面交
        """
        res = fallback_regex_parse(title, content)
        assert res["category"] == "SSD"
        assert "1TB" in res["spec_group"] or "SSD" in res["spec_group"]
        assert res["brand"] == "Samsung"
        assert res["used_asking_price"] == 1800
        assert res["warranty_status"] == "過保"
        assert res["seller_type"] == "個人自售"

    def test_cpu_post_miner_bulk_seller(self):
        """測試 CPU / 礦老闆大量工作室特徵辨識。"""
        title = "[賣/全國/郵寄] AMD R7 7800X3D 大量多組出清"
        content = """
        ◎硬體型號：AMD Ryzen 7 7800X3D
        ◎欲售價格：11500 / 顆
        ◎品樣狀況：工作室大量退役機器拆賣，共有 8 組，多卡多組一次全收可議
        ◎交易方式：郵寄 / 宅配
        """
        res = fallback_regex_parse(title, content)
        assert res["category"] == "CPU"
        assert "7800X3D" in res["spec_group"]
        assert res["brand"] == "AMD"
        assert res["used_asking_price"] == 11500
        assert res["seller_type"] == "工作室/礦老闆"


class TestParsePostWithLLM:
    """測試 parse_post_with_llm 的降級與容錯行為。"""

    def test_no_api_key_graceful_fallback(self):
        """當未提供 API Key 且環境變數為空時，自動降級為正則解析，絕不崩潰。"""
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            res = parse_post_with_llm(
                title="[賣/台北/面交] 華碩 RTX 4060 Ti 8G",
                content="◎硬體型號：ASUS RTX 4060 Ti 8G\n◎欲售價格：11000\n◎品樣狀況：保內",
                api_key="",
            )
            assert isinstance(res, dict)
            assert res["category"] == "GPU"
            assert res["brand"] == "ASUS"
            assert res["used_asking_price"] == 11000
            assert res["warranty_status"] == "原廠保內"

    def test_api_network_error_fallback(self):
        """當 API 調用發生網路異常或超時，自動降級為正則備援。"""
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch(
                "requests.post",
                side_effect=requests.exceptions.ConnectionError("Connection timed out"),
            ):
                res = parse_post_with_llm(
                    title="[賣/新竹/店到店] 金士頓 DDR5-5600 32G",
                    content="◎硬體型號：Kingston DDR5-5600 32G\n◎欲售價格：2600\n◎品樣狀況：過保",
                )
                assert isinstance(res, dict)
                assert res["category"] == "RAM"
                assert res["brand"] == "Kingston"
                assert res["used_asking_price"] == 2600
                assert res["warranty_status"] == "過保"

    def test_api_http_500_fallback(self):
        """當 API 回傳 HTTP 500 或 429 Rate Limit 時，自動降級。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch("requests.post", return_value=mock_resp):
                res = parse_post_with_llm(
                    title="[賣/台中/面交] Intel i5-13600K",
                    content="◎硬體型號：Intel i5-13600K\n◎欲售價格：6800\n◎品樣狀況：原廠保內",
                )
                assert isinstance(res, dict)
                assert res["category"] == "CPU"
                assert res["used_asking_price"] == 6800

    def test_api_malformed_json_fallback(self):
        """當 API 回傳非合規 JSON 字串時，自動降級為正則抽取。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "This is definitely not a JSON object."}}]
        }

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch("requests.post", return_value=mock_resp):
                res = parse_post_with_llm(
                    title="[賣/台南/面交] 技嘉 RTX 4060 Ti 8G",
                    content="◎硬體型號：Gigabyte RTX 4060 Ti 8G\n◎欲售價格：10200\n◎品樣狀況：保內",
                )
                assert isinstance(res, dict)
                assert res["category"] == "GPU"
                assert res["used_asking_price"] == 10200

    def test_api_success_json_parsing(self):
        """當 API 正常回傳標準 JSON 時，準確解析並格式化。"""
        llm_json = {
            "category": "GPU",
            "spec_group": "RTX 4060 Ti 8G",
            "brand": "ASUS",
            "model_name": "DUAL-RTX4060TI-O8G",
            "used_asking_price": 10500,
            "warranty_status": "原廠保內",
            "seller_type": "個人自售",
            "trade_method": "雙北面交 / 賣貨便",
            "condition_notes": "盒裝完整",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": f"```json\n{json.dumps(llm_json)}\n```"}}]
        }

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch("requests.post", return_value=mock_resp):
                res = parse_post_with_llm(
                    title="[賣/台北/面交] 華碩 RTX 4060 Ti 8G",
                    content="內文",
                )
                assert res["category"] == "GPU"
                assert res["spec_group"] == "RTX 4060 Ti 8G"
                assert res["brand"] == "ASUS"
                assert res["used_asking_price"] == 10500
                assert res["warranty_status"] == "原廠保內"
                assert res["trade_method"] == "雙北面交 / 賣貨便"


# ==============================================================================
# parse_post_with_llm 例外處理兩層規則回歸測試（只捕捉具體例外、失敗要記 warning）
# ==============================================================================

_LLM_LOGGER = "cleaners.llm_parser"
_TITLE = "[賣/新竹/店到店] 金士頓 DDR5-5600 32G"
_CONTENT = "◎硬體型號：Kingston DDR5-5600 32G\n◎欲售價格：2600\n◎品樣狀況：過保"


def _ok_resp(llm_content: Any) -> MagicMock:
    """建立 status 200、choices[0].message.content 為指定內容的 mock 回應。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": llm_content}}]}
    return resp


def _call_llm(**post_kwargs: Any) -> dict[str, Any]:
    """在假 API key 下呼叫 parse_post_with_llm，requests.post 以 post_kwargs 設定的 mock 取代。"""
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
        with patch("requests.post", **post_kwargs) as mock_post:
            res = parse_post_with_llm(title=_TITLE, content=_CONTENT)
            assert mock_post.called
            return res


def _llm_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LLM_LOGGER and r.levelno >= logging.WARNING]


class TestParsePostWithLLMExceptionHandling:
    """LLM 失敗時回傳 fallback_regex_parse 結果並記 warning；非預期例外不得被吞掉。"""

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.ConnectionError("Connection refused"),
            requests.exceptions.Timeout("Read timed out"),
            requests.exceptions.SSLError("bad handshake"),
        ],
        ids=["ConnectionError", "Timeout", "SSLError"],
    )
    def test_request_exception_falls_back_with_warning(
        self, exc: Exception, caplog: pytest.LogCaptureFixture
    ):
        """requests 的各種網路例外 → 回傳與 fallback_regex_parse 完全相同的結果，並記 warning（含錯誤訊息）。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        res = _call_llm(side_effect=exc)
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        warnings = _llm_warnings(caplog)
        assert warnings, "網路錯誤降級時沒有記 warning（靜默失敗）"
        assert str(exc) in warnings[0].getMessage()

    @pytest.mark.parametrize("status", [404, 429, 500], ids=["404-model-gone", "429", "500"])
    def test_non_200_falls_back_with_warning(
        self, status: int, caplog: pytest.LogCaptureFixture
    ):
        """非 200（含 ERR-013 的模型失效 404）→ 回傳 fallback 結果，且要記 warning，不能靜默。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        resp = MagicMock()
        resp.status_code = status
        resp.text = "error"
        res = _call_llm(return_value=resp)
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        assert _llm_warnings(caplog), f"HTTP {status} 降級為 fallback 時沒有記 warning（靜默失敗）"

    def test_response_body_not_json_falls_back_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        """resp.json() 拋 requests 的 JSONDecodeError（ValueError 子類）→ fallback + warning。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        res = _call_llm(return_value=resp)
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        assert _llm_warnings(caplog)

    @pytest.mark.parametrize(
        "body",
        [
            {},  # 缺 choices → KeyError
            {"error": {"message": "rate limited"}},  # OpenRouter 錯誤 payload → KeyError
            {"choices": []},  # 空 choices → IndexError
            {"choices": [{"message": {}}]},  # 缺 content → KeyError
            {"choices": [{"message": {"content": None}}]},  # content 為 None → AttributeError
            {"choices": None},  # choices 為 None → TypeError
        ],
        ids=["no-choices", "error-payload", "empty-choices", "no-content", "content-None", "choices-None"],
    )
    def test_malformed_api_payload_falls_back_with_warning(
        self, body: dict[str, Any], caplog: pytest.LogCaptureFixture
    ):
        """API 回傳結構不完整（KeyError／IndexError／TypeError／AttributeError）→ fallback + warning。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = body
        res = _call_llm(return_value=resp)
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        assert _llm_warnings(caplog)

    @pytest.mark.parametrize(
        "llm_content",
        [
            "This is definitely not a JSON object.",  # 非 JSON → JSONDecodeError
            '[{"spec_group": "DDR5-5600 32G"}]',  # JSON 陣列 → list 沒有 .get
            '"DDR5-5600 32G"',  # JSON 字串
            "12345",  # JSON 數字
            "null",  # JSON null
            "```json\n[1, 2, 3]\n```",  # code fence 包著的陣列
            '{"used_asking_price": "面議"}',  # 價格不是數字 → int() ValueError
            '{"used_asking_price": [2600]}',  # 價格是陣列 → int() TypeError
        ],
        ids=["not-json", "json-array", "json-string", "json-number", "json-null",
             "fenced-array", "price-text", "price-list"],
    )
    def test_llm_content_not_usable_dict_falls_back_with_warning(
        self, llm_content: str, caplog: pytest.LogCaptureFixture
    ):
        """LLM 回傳非 dict 或欄位型別錯誤 → 整筆退回 fallback 結果並記 warning，不崩潰。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        res = _call_llm(return_value=_ok_resp(llm_content))
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        assert _llm_warnings(caplog)

    def test_llm_price_infinity_falls_back_with_warning(self, caplog: pytest.LogCaptureFixture):
        """json 接受 Infinity，int(inf) 拋 OverflowError → 退回 fallback 並記 warning，不中止整批同步。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        res = _call_llm(return_value=_ok_resp('{"used_asking_price": Infinity}'))
        assert res == fallback_regex_parse(_TITLE, _CONTENT)
        assert _llm_warnings(caplog)

    def test_unexpected_exception_from_requests_propagates(self):
        """非預期例外（例如 RuntimeError，代表程式 bug）不在捕捉清單內，必須往外拋而不是被吞成 fallback。"""
        with pytest.raises(RuntimeError, match="boom"):
            _call_llm(side_effect=RuntimeError("boom"))

    def test_unexpected_exception_while_reading_response_propagates(self):
        """解析回應時發生非預期例外（例如 RuntimeError）也要往外拋。"""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = RuntimeError("decoder bug")
        with pytest.raises(RuntimeError, match="decoder bug"):
            _call_llm(return_value=resp)

    def test_success_path_does_not_warn(self, caplog: pytest.LogCaptureFixture):
        """正常回傳有效 JSON 時不應記任何 warning（避免誤報）。"""
        caplog.set_level(logging.WARNING, logger=_LLM_LOGGER)
        llm_json = {"category": "RAM", "spec_group": "DDR5-5600 32G", "used_asking_price": 2550}
        res = _call_llm(return_value=_ok_resp(json.dumps(llm_json)))
        assert res["spec_group"] == "DDR5-5600 32G"
        assert res["used_asking_price"] == 2550
        assert _llm_warnings(caplog) == []

    def test_llm_zero_price_uses_fallback_price(self):
        """LLM 回傳 0（未開價）但內文有價格 → 採用 fallback 解析出的 2600，不把真實價格變成 0（ERR-002）。"""
        llm_json = {"category": "RAM", "spec_group": "DDR5-5600 32G", "used_asking_price": 0}
        res = _call_llm(return_value=_ok_resp(json.dumps(llm_json)))
        assert res["used_asking_price"] == 2600

    def test_llm_and_fallback_both_no_price_keeps_zero(self):
        """LLM 與 fallback 都解析不到價格 → 存 0（未開價標記，ERR-015），型別為 int、不是 None。"""
        llm_json = {"category": "RAM", "spec_group": "DDR5-5600 32G", "used_asking_price": None}
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch("requests.post", return_value=_ok_resp(json.dumps(llm_json))):
                res = parse_post_with_llm(
                    title="[賣/新竹/店到店] 金士頓 DDR5-5600 32G",
                    content="◎硬體型號：Kingston DDR5-5600 32G\n◎品樣狀況：過保，價格私訊",
                )
        assert res["used_asking_price"] == 0
        assert isinstance(res["used_asking_price"], int)

    def test_requests_post_called_with_timeout(self):
        """網路請求一定要帶 timeout（AGENTS.md）。"""
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-fake-key"}):
            with patch("requests.post", return_value=_ok_resp("{}")) as mock_post:
                parse_post_with_llm(title=_TITLE, content=_CONTENT, timeout=7)
        assert mock_post.call_args.kwargs.get("timeout") == 7

"""cleaners/resanitize_db.py 回歸測試。

驗證 resanitize_database() 的回傳結構、4 則 INFO 進度訊息（由 print 改為 logging），
以及 `python -m cleaners.resanitize_db <db>` 直接執行時終端仍看得到進度與摘要。
全部使用 tmp_path 暫存 SQLite，完全離線：PTT session、內文抓取與 LLM 解析一律 mock。
"""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from cleaners import resanitize_db
from cleaners.resanitize_db import resanitize_database
from database.db_manager import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_KEYS = {"deleted_products", "updated_ptt", "updated_fb", "total_used"}
PROGRESS_PREFIXES = (
    "[*] 開始執行硬體資料庫全量清洗",
    "  [1/3] 已清除 ",
    "  [2/3] 已完成 ",
    "  [3/3] 已完成 ",
)


@pytest.fixture
def offline() -> dict[str, MagicMock]:
    """擋住所有可能連網的呼叫：PTT session、內文抓取、LLM 解析與 sleep。"""
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=200,
        text='<html><head><meta property="og:title" content="[賣/台北] RTX 3060 12G"></head></html>',
    )
    with (
        patch.object(resanitize_db, "_get_ptt_session", return_value=session) as m_session,
        patch.object(resanitize_db, "fetch_ptt_post_details", return_value={"content": "售價 8000"}) as m_fetch,
        patch.object(
            resanitize_db,
            "parse_post_with_llm",
            return_value={
                "spec_group": "RTX 3060 12G",
                "used_asking_price": 8000,
                "seller_type": "個人自售",
                "warranty_status": "過保",
                "trade_method": "面交",
            },
        ) as m_llm,
        patch.object(resanitize_db.time, "sleep") as m_sleep,
    ):
        yield {"session": session, "get_session": m_session, "fetch": m_fetch, "llm": m_llm, "sleep": m_sleep}


def _progress_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "resanitize_db"]


def _seed(db_path: Path) -> None:
    """塞入 1 筆污染 GPU 配件、1 筆 PTT 貼文、2 筆 FB 貼文。"""
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO products (category, spec_group, brand, model_name) VALUES ('GPU', 'RTX 3060 12G', 'Leadtek', '麗臺 mini-DP 轉接線');"
    )
    conn.execute(
        "INSERT INTO products (category, spec_group, brand, model_name) VALUES ('GPU', 'RTX 3060 12G', 'MSI', 'MSI RTX 3060 VENTUS 12G');"
    )
    rows = [
        ("PTT_1", "PTT HardwareSale", "[賣", 0, "https://www.ptt.cc/bbs/HardwareSale/M.1.A.html"),
        ("FB_c80db8157c57", "Facebook", "Facebook", 0, "https://www.facebook.com/groups/x/posts/1"),
        ("FB_other", "FB 二手社團", "【徵】RTX 4070", 17000, "https://www.facebook.com/groups/x/posts/2"),
    ]
    for post_id, platform, spec, price, url in rows:
        conn.execute(
            """
            INSERT INTO used_market_listings
                (post_id, spec_group, source_platform, seller_name, seller_type, used_asking_price,
                 warranty_status, sale_status, post_time, post_url)
            VALUES (?, ?, ?, 'seller', '個人自售', ?, '過保', '販售中', '2026-11-01T00:00:00', ?);
            """,
            (post_id, spec, platform, price, url),
        )
    conn.commit()
    conn.close()


def test_empty_db_returns_dict_with_expected_keys_and_zero_counts(tmp_path: Path, offline: dict) -> None:
    result = resanitize_database(str(tmp_path / "empty.db"))

    assert isinstance(result, dict)
    assert set(result) == EXPECTED_KEYS
    assert result == {"deleted_products": 0, "updated_ptt": 0, "updated_fb": 0, "total_used": 0}
    assert all(type(v) is int for v in result.values())
    offline["session"].get.assert_not_called()
    offline["llm"].assert_not_called()


def test_empty_db_emits_four_info_progress_messages(
    tmp_path: Path, offline: dict, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = str(tmp_path / "empty.db")
    caplog.set_level(logging.INFO, logger="resanitize_db")

    resanitize_database(db_path)

    records = _progress_records(caplog)
    messages = [r.getMessage() for r in records]
    assert len(records) == 4, messages
    assert all(r.levelno == logging.INFO for r in records)
    for prefix, msg in zip(PROGRESS_PREFIXES, messages):
        assert msg.startswith(prefix), (prefix, msg)
    assert messages[0] == f"[*] 開始執行硬體資料庫全量清洗 (DB: {db_path})..."
    assert messages[1] == "  [1/3] 已清除 0 筆非顯卡轉接線/配件資料。"
    assert messages[2] == "  [2/3] 已完成 0 筆 PTT 貼文重構與價格回填。"
    assert messages[3] == "  [3/3] 已完成 0 筆 FB 貼文標籤與價格修復。"


def test_progress_messages_not_printed_to_stdout_when_called_as_library(
    tmp_path: Path, offline: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """函式庫模組不該 print；進度只經由 logging 輸出。"""
    resanitize_database(str(tmp_path / "empty.db"))

    out = capsys.readouterr().out
    assert "開始執行硬體資料庫全量清洗" not in out
    assert "[1/3]" not in out


def test_seeded_db_counts_match_log_messages_and_total(
    tmp_path: Path, offline: dict, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "seeded.db"
    _seed(db_path)
    caplog.set_level(logging.INFO, logger="resanitize_db")

    result = resanitize_database(str(db_path))

    assert result == {"deleted_products": 1, "updated_ptt": 1, "updated_fb": 2, "total_used": 3}
    messages = [r.getMessage() for r in _progress_records(caplog)]
    assert messages[1] == "  [1/3] 已清除 1 筆非顯卡轉接線/配件資料。"
    assert messages[2] == "  [2/3] 已完成 1 筆 PTT 貼文重構與價格回填。"
    assert messages[3] == "  [3/3] 已完成 2 筆 FB 貼文標籤與價格修復。"
    offline["llm"].assert_called_once()

    conn = init_db(str(db_path))
    try:
        models = {r[0] for r in conn.execute("SELECT model_name FROM products;")}
        assert models == {"MSI RTX 3060 VENTUS 12G"}
        ptt = conn.execute("SELECT spec_group, used_asking_price FROM used_market_listings WHERE post_id='PTT_1';").fetchone()
        assert (ptt[0], ptt[1]) == ("RTX 3060 12G", 8000)
        fb = conn.execute(
            "SELECT spec_group, used_asking_price FROM used_market_listings WHERE post_id='FB_c80db8157c57';"
        ).fetchone()
        assert (fb[0], fb[1]) == ("RTX 4090 24G", 52000)
    finally:
        conn.close()


def test_cli_shows_progress_messages_and_summary(tmp_path: Path) -> None:
    """`python -m cleaners.resanitize_db <db>` 直接執行時，終端要看得到 4 則進度與最後摘要。

    空資料庫不會有任何 PTT 貼文，因此不會發出網路請求或呼叫 LLM。
    """
    db_path = tmp_path / "cli.db"
    proc = subprocess.run(
        [sys.executable, "-m", "cleaners.resanitize_db", str(db_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=60,
    )
    stdout = proc.stdout.decode("utf-8")
    stderr = proc.stderr.decode("utf-8")
    terminal = stdout + stderr

    assert proc.returncode == 0, terminal
    assert "Traceback" not in terminal
    for prefix in PROGRESS_PREFIXES:
        assert prefix.strip() in terminal, (prefix, terminal)
    assert "[OK] 資料庫清洗完成！摘要：" in stdout
    assert "'total_used': 0" in stdout

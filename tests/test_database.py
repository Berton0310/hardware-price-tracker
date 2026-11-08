"""單元測試：M1 資料庫模組驗收 (tests/test_database.py)

完全獨立客觀驗證 database/db_manager.py：
1. 資料表與索引完整性
2. 外鍵約束強制生效 (PRAGMA foreign_keys = ON)
3. upsert_product 正常新增、衝突更新與 ID 冪等性
4. record_new_price 每日記錄與同日覆蓋更新 (UPSERT)
5. upsert_used_listing 二手貼文新增、更新與 NULL product_id 支援
6. 級聯刪除 (ON DELETE CASCADE) 與外鍵置空 (ON DELETE SET NULL)
7. get_product_price_history 時間區間過濾與全量查詢
8. get_used_market_by_spec 規格過濾與降序排列
9. 邊界與異常情境（磁碟路徑建置、必填欄位缺失、NOT NULL 約束）
"""

import datetime
import sqlite3
import pytest

from database.db_manager import (
    init_db,
    upsert_product,
    record_new_price,
    upsert_used_listing,
    get_product_price_history,
    get_used_market_by_spec,
    record_sync_log,
    get_latest_sync_log,
)


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """提供乾淨的 In-Memory SQLite 連線。"""
    conn = init_db(":memory:")
    yield conn
    conn.close()


class TestDatabaseSchemaAndInit:
    """資料表結構、索引與連線初始化驗證。"""

    def test_tables_and_indexes_exist(self, mem_db: sqlite3.Connection):
        """驗證所有預期資料表與索引均成功建立。"""
        cursor = mem_db.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index');"
        )
        items = {row["name"]: row["type"] for row in cursor.fetchall()}

        # 驗證資料表
        expected_tables = ["products", "new_price_history", "used_market_listings"]
        for tbl in expected_tables:
            assert tbl in items, f"資料表 {tbl} 未建立"
            assert items[tbl] == "table"

        # 驗證索引
        expected_indexes = [
            "idx_products_spec_group",
            "idx_price_date",
            "idx_used_spec_group",
            "idx_used_post_time",
        ]
        for idx in expected_indexes:
            assert idx in items, f"索引 {idx} 未建立"
            assert items[idx] == "index"

    def test_foreign_keys_pragma_enabled(self, mem_db: sqlite3.Connection):
        """驗證外鍵約束是否確實啟用。"""
        cursor = mem_db.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        assert fk_status == 1, "SQLite 外鍵約束未啟用"

    def test_init_db_creates_parent_directory_and_sets_wal(self, tmp_path):
        """驗證磁碟模式下能自動建立不存在的父目錄並啟用 WAL 模式。"""
        db_file = tmp_path / "deep" / "nested" / "storage" / "hardware_test.db"
        assert not db_file.parent.exists()

        conn = init_db(str(db_file))
        assert db_file.exists()

        # 驗證 WAL 模式
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal", f"Journal mode 應為 WAL，實際為 {mode}"

        conn.close()


class TestForeignKeyIntegrity:
    """外鍵約束與資料完整性驗證。"""

    def test_new_price_history_rejects_invalid_product_id(self, mem_db: sqlite3.Connection):
        """無效的 product_id 寫入新品價格表必須觸發 IntegrityError。"""
        with pytest.raises(sqlite3.IntegrityError):
            record_new_price(
                conn=mem_db,
                product_id=99999,
                record_date="2026-11-01",
                listed_price=10000,
                current_price=9500,
            )

    def test_used_listing_rejects_invalid_product_id(self, mem_db: sqlite3.Connection):
        """二手貼文若提供不存在的 product_id 必須觸發 IntegrityError。"""
        post_data = {
            "post_id": "PTT_INVALID_FK",
            "product_id": 88888,
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT",
            "seller_name": "test_seller",
            "used_asking_price": 12000,
            "post_time": "2026-11-01 12:00:00",
        }
        with pytest.raises(sqlite3.IntegrityError):
            upsert_used_listing(mem_db, post_data)

    def test_used_listing_allows_null_product_id(self, mem_db: sqlite3.Connection):
        """二手貼文之 product_id 允許為 NULL (尚未關聯到標準品)。"""
        post_data = {
            "post_id": "PTT_NULL_FK",
            "product_id": None,
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT",
            "seller_name": "test_seller",
            "used_asking_price": 12000,
            "post_time": "2026-11-01 12:00:00",
        }
        upsert_used_listing(mem_db, post_data)
        rows = get_used_market_by_spec(mem_db, "RTX 4060 Ti 16G")
        assert len(rows) == 1
        assert rows[0]["product_id"] is None


class TestUpsertProduct:
    """產品主檔插入與 UPSERT 邏輯驗證。"""

    def test_insert_and_conflict_update(self, mem_db: sqlite3.Connection):
        """驗證新增產品主檔與相同 model_name 更新時的正確性與 ID 不變性。"""
        pid1 = upsert_product(
            conn=mem_db,
            category="GPU",
            spec_group="RTX 4060 Ti 16G",
            brand="ASUS",
            model_name="DUAL-RTX4060TI-O16G",
            key_specs="雙風扇 / 16G GDDR6",
            warranty_years=3,
        )
        assert isinstance(pid1, int)
        assert pid1 > 0

        # 重複插入相同 model_name，但更新規格備註與保固年限
        pid2 = upsert_product(
            conn=mem_db,
            category="GPU",
            spec_group="RTX 4060 Ti 16G",
            brand="ASUS",
            model_name="DUAL-RTX4060TI-O16G",
            key_specs="雙風扇 / 16G GDDR6 / 官網登錄+1年",
            warranty_years=4,
        )
        assert pid1 == pid2, "重複插入相同 model_name 必須回傳相同 product_id"

        cursor = mem_db.execute(
            "SELECT * FROM products WHERE product_id = ?;", (pid1,)
        )
        row = cursor.fetchone()
        assert row["key_specs"] == "雙風扇 / 16G GDDR6 / 官網登錄+1年"
        assert row["warranty_years"] == 4

        # 確認總筆數維持 1 筆
        count = mem_db.execute("SELECT COUNT(*) FROM products;").fetchone()[0]
        assert count == 1

    def test_upsert_product_not_null_violation(self, mem_db: sqlite3.Connection):
        """缺少必填欄位 (NOT NULL) 必須引發 IntegrityError。"""
        with pytest.raises(sqlite3.IntegrityError):
            upsert_product(
                conn=mem_db,
                category=None,  # type: ignore
                spec_group="RTX 4060 Ti",
                brand="MSI",
                model_name="TEST-NULL",
            )


class TestRecordNewPrice:
    """新品歷史價格記錄與同日冪等性更新驗證。"""

    def test_insert_and_idempotent_update(self, mem_db: sqlite3.Connection):
        """同日同產品重複記錄時，必須覆蓋更新價格與狀態而不報唯一鍵衝突。"""
        pid = upsert_product(
            mem_db, "GPU", "RTX 4060 Ti 16G", "MSI", "MSI-VENTUS-2X-16G"
        )

        record_new_price(
            conn=mem_db,
            product_id=pid,
            record_date="2026-11-01",
            listed_price=15990,
            current_price=14990,
            change_tag="不變",
            in_stock=True,
        )

        history = get_product_price_history(mem_db, pid, days=0)
        assert len(history) == 1
        assert history[0]["current_new_price"] == 14990
        assert history[0]["in_stock"] == 1

        # 當天下午價格調降，再次寫入
        record_new_price(
            conn=mem_db,
            product_id=pid,
            record_date="2026-11-01",
            listed_price=15990,
            current_price=13990,
            change_tag="↘降價",
            in_stock=False,
        )

        history_updated = get_product_price_history(mem_db, pid, days=0)
        assert len(history_updated) == 1, "同日紀錄應被覆蓋，總筆數仍為 1"
        assert history_updated[0]["current_new_price"] == 13990
        assert history_updated[0]["price_change_tag"] == "↘降價"
        assert history_updated[0]["in_stock"] == 0


class TestUpsertUsedListing:
    """二手市場貼文與行情更新驗證。"""

    def test_insert_and_conflict_update(self, mem_db: sqlite3.Connection):
        """二手貼文首次插入後，若相同 post_id 再次寫入，需更新價格與狀態。"""
        pid = upsert_product(
            mem_db, "GPU", "RTX 4060 Ti 16G", "GIGABYTE", "GV-N406TEAGLE-16GD"
        )

        post = {
            "post_id": "PTT_1730510001",
            "product_id": pid,
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT",
            "seller_name": "hard_buyer",
            "seller_type": "個人自售",
            "used_asking_price": 12500,
            "warranty_status": "原廠保內",
            "trade_method": "面交/宅配",
            "comment_count": 3,
            "sale_status": "販售中",
            "post_time": "2026-11-01 10:30:00",
            "post_url": "https://www.ptt.cc/bbs/HardwareSale/M.1730510001.A.html",
        }
        upsert_used_listing(mem_db, post)

        rows = get_used_market_by_spec(mem_db, "RTX 4060 Ti 16G")
        assert len(rows) == 1
        assert rows[0]["used_asking_price"] == 12500
        assert rows[0]["sale_status"] == "販售中"

        # 賣家改價降為 11500 並標示已售出
        post["used_asking_price"] = 11500
        post["sale_status"] = "已售出"
        post["comment_count"] = 15
        upsert_used_listing(mem_db, post)

        rows_after = get_used_market_by_spec(mem_db, "RTX 4060 Ti 16G")
        assert len(rows_after) == 1
        assert rows_after[0]["used_asking_price"] == 11500
        assert rows_after[0]["sale_status"] == "已售出"
        assert rows_after[0]["comment_count"] == 15

    def test_missing_mandatory_keys_raises_key_error(self, mem_db: sqlite3.Connection):
        """缺少必要欄位（例如缺少 post_id 或 used_asking_price）時引發 KeyError。"""
        incomplete_post = {
            "product_id": None,
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT",
            # 缺少 post_id, seller_name, used_asking_price, post_time
        }
        with pytest.raises(KeyError):
            upsert_used_listing(mem_db, incomplete_post)

    def test_defaults_and_null_comment_count(self, mem_db: sqlite3.Connection):
        """驗證選填欄位缺省時的預設值與 comment_count=None 的容錯處理。"""
        minimal_post = {
            "post_id": "PTT_MINIMAL",
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT",
            "seller_name": "minimal_user",
            "used_asking_price": 10000,
            "post_time": "2026-11-01 12:00:00",
            "comment_count": None,  # 測試 None 轉為 0
        }
        upsert_used_listing(mem_db, minimal_post)

        rows = get_used_market_by_spec(mem_db, "RTX 4060 Ti 16G")
        assert len(rows) == 1
        assert rows[0]["seller_type"] == "個人自售"
        assert rows[0]["warranty_status"] == "過保"
        assert rows[0]["sale_status"] == "販售中"
        assert rows[0]["comment_count"] == 0
        assert rows[0]["post_url"] == ""


class TestCascadeAndConstraints:
    """級聯刪除 (CASCADE) 與置空 (SET NULL) 驗證。"""

    def test_delete_product_cascades_history_and_sets_null_used(
        self, mem_db: sqlite3.Connection
    ):
        """驗證刪除 product 主檔時：
        1. new_price_history 自動級聯刪除 (ON DELETE CASCADE)
        2. used_market_listings 外鍵置為 NULL (ON DELETE SET NULL)
        """
        pid = upsert_product(
            mem_db, "GPU", "RTX 4070 12G", "ASUS", "TUF-RTX4070-O12G"
        )

        # 記錄新品價格
        record_new_price(mem_db, pid, "2026-11-01", 20990, 19990)
        record_new_price(mem_db, pid, "2026-11-02", 20990, 19500)

        # 記錄二手貼文
        post = {
            "post_id": "FB_CASCADE_TEST",
            "product_id": pid,
            "spec_group": "RTX 4070 12G",
            "source_platform": "FB_Group",
            "seller_name": "fb_user",
            "used_asking_price": 16000,
            "post_time": "2026-11-02 18:00:00",
        }
        upsert_used_listing(mem_db, post)

        # 刪除 product 主檔
        mem_db.execute("DELETE FROM products WHERE product_id = ?;", (pid,))
        mem_db.commit()

        # 驗證 1: 新品價格已被級聯清空
        cur_hist = mem_db.execute(
            "SELECT COUNT(*) FROM new_price_history WHERE product_id = ?;", (pid,)
        )
        assert cur_hist.fetchone()[0] == 0, "新品歷史價格未被級聯刪除"

        # 驗證 2: 二手貼文依然存在，但 product_id 被置為 NULL
        cur_used = mem_db.execute(
            "SELECT product_id FROM used_market_listings WHERE post_id = ?;",
            ("FB_CASCADE_TEST",),
        )
        used_row = cur_used.fetchone()
        assert used_row is not None, "二手貼文不應被刪除"
        assert used_row["product_id"] is None, "二手貼文之 product_id 應被置為 NULL"


class TestQueryFunctions:
    """查詢函式邏輯、過濾與排序驗證。"""

    def test_get_product_price_history_days_filtering(self, mem_db: sqlite3.Connection):
        """驗證近 N 日價格查詢與全量查詢。"""
        pid = upsert_product(
            mem_db, "RAM", "DDR5 6000 32G", "Kingston", "KF560C36BBEK2-32"
        )

        today = datetime.date.today()
        d_today = today.strftime("%Y-%m-%d")
        d_10_days_ago = (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        d_40_days_ago = (today - datetime.timedelta(days=40)).strftime("%Y-%m-%d")
        d_100_days_ago = (today - datetime.timedelta(days=100)).strftime("%Y-%m-%d")

        # 插入四個不同時間點的價格
        record_new_price(mem_db, pid, d_100_days_ago, 3500, 3500)
        record_new_price(mem_db, pid, d_40_days_ago, 3500, 3300)
        record_new_price(mem_db, pid, d_10_days_ago, 3500, 3100)
        record_new_price(mem_db, pid, d_today, 3500, 2990)

        # 查詢近 30 日：應包含今天與 10 天前（共 2 筆）
        res_30 = get_product_price_history(mem_db, pid, days=30)
        assert len(res_30) == 2
        dates_30 = [r["record_date"] for r in res_30]
        assert dates_30 == [d_10_days_ago, d_today], "排序應為日期升序 (ASC)"

        # 查詢近 60 日：應包含今天、10 天前、40 天前（共 3 筆）
        res_60 = get_product_price_history(mem_db, pid, days=60)
        assert len(res_60) == 3

        # 查詢全量 (days=0 或 days=-1)
        res_all_0 = get_product_price_history(mem_db, pid, days=0)
        assert len(res_all_0) == 4

        res_all_neg = get_product_price_history(mem_db, pid, days=-1)
        assert len(res_all_neg) == 4

    def test_get_used_market_by_spec_sorting_and_filtering(
        self, mem_db: sqlite3.Connection
    ):
        """驗證規格群組二手查詢之精確過濾與時間倒序排列。"""
        target_spec = "RTX 4060 Ti 16G"
        other_spec = "RTX 4070 SUPER 12G"

        posts = [
            {
                "post_id": "P1",
                "spec_group": target_spec,
                "source_platform": "PTT",
                "seller_name": "user_a",
                "used_asking_price": 12000,
                "post_time": "2026-11-01 08:00:00",
            },
            {
                "post_id": "P2",
                "spec_group": target_spec,
                "source_platform": "FB_Group",
                "seller_name": "user_b",
                "used_asking_price": 11800,
                "post_time": "2026-11-02 20:00:00",
            },
            {
                "post_id": "P3",
                "spec_group": target_spec,
                "source_platform": "PTT",
                "seller_name": "user_c",
                "used_asking_price": 12200,
                "post_time": "2026-11-01 15:30:00",
            },
            {
                "post_id": "P_OTHER",
                "spec_group": other_spec,
                "source_platform": "PTT",
                "seller_name": "user_d",
                "used_asking_price": 18000,
                "post_time": "2026-11-02 21:00:00",
            },
        ]

        for p in posts:
            upsert_used_listing(mem_db, p)

        results = get_used_market_by_spec(mem_db, target_spec)
        assert len(results) == 3

        # 時間降序：P2 (11-02 20:00) > P3 (11-01 15:30) > P1 (11-01 08:00)
        assert [r["post_id"] for r in results] == ["P2", "P3", "P1"]

        # 查不存在規格回傳空串列
        assert get_used_market_by_spec(mem_db, "NON_EXISTENT_SPEC") == []

    def test_get_product_price_history_empty_result(self, mem_db: sqlite3.Connection):
        """驗證查無資料時回傳空串列。"""
        assert get_product_price_history(mem_db, product_id=99999, days=30) == []
        assert get_product_price_history(mem_db, product_id=99999, days=0) == []


class TestSyncLogsPersistence:
    """驗證 sync_logs 資料表建置、寫入與最新記錄查詢功能。"""

    def test_empty_sync_logs_returns_none(self, mem_db: sqlite3.Connection):
        """空資料表下查詢最新同步日誌應回傳 None。"""
        assert get_latest_sync_log(mem_db) is None

    def test_record_sync_log_and_retrieve_latest(self, mem_db: sqlite3.Connection):
        """插入單筆同步記錄並檢驗欄位完整性。"""
        log_id = record_sync_log(
            mem_db,
            status="SUCCESS",
            details="原價屋: 20 筆, PTT: 10 筆, FB: 0 筆",
            coolpc_count=20,
            ptt_count=10,
            fb_count=0,
            sync_time="2026-11-03T12:00:00",
        )
        assert isinstance(log_id, int)
        assert log_id > 0

        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["log_id"] == log_id
        assert latest["status"] == "SUCCESS"
        assert latest["coolpc_count"] == 20
        assert latest["ptt_count"] == 10
        assert latest["fb_count"] == 0
        assert latest["sync_time"] == "2026-11-03T12:00:00"
        assert "原價屋: 20 筆" in latest["details"]

    def test_multiple_sync_logs_order(self, mem_db: sqlite3.Connection):
        """插入多筆不同時間與狀態的日誌，驗證以 sync_time DESC, log_id DESC 取出最新一筆。"""
        record_sync_log(
            mem_db,
            status="SUCCESS",
            details="First",
            coolpc_count=5,
            ptt_count=5,
            fb_count=0,
            sync_time="2026-11-01T10:00:00",
        )
        record_sync_log(
            mem_db,
            status="FAILED",
            details="Second (Error)",
            coolpc_count=0,
            ptt_count=0,
            fb_count=0,
            sync_time="2026-11-02T10:00:00",
        )
        third_id = record_sync_log(
            mem_db,
            status="SUCCESS",
            details="Third (Latest)",
            coolpc_count=15,
            ptt_count=8,
            fb_count=2,
            sync_time="2026-11-03T10:00:00",
        )

        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["log_id"] == third_id
        assert latest["status"] == "SUCCESS"
        assert latest["coolpc_count"] == 15
        assert latest["ptt_count"] == 8
        assert latest["fb_count"] == 2

    def test_record_sync_log_default_time(self, mem_db: sqlite3.Connection):
        """若未傳遞 sync_time，應自動填入當前 ISO 格式時間戳記。"""
        log_id = record_sync_log(mem_db, status="SUCCESS", details="Auto time")
        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["log_id"] == log_id
        assert "T" in latest["sync_time"]


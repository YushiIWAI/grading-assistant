"""Step 3: 整合性・堅牢性に関するテスト

1. delete_school_data が api_keys も削除する
2. 監査ログの単一トランザクション統合（チェーン検証で確認）
3. verify_audit_chain がページングで全件走査する
4. login / mfa/verify のレート制限
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.app import app, _login_limiter, _mfa_verify_limiter
from auth import hash_password
from models import School, User
import storage
from storage import log_audit_event, verify_audit_chain

client = TestClient(app)


class TestDeleteSchoolDataWithApiKeys:
    """delete_school_data が api_keys も削除する"""

    def test_api_keys_deleted_with_school(self, test_db, test_school, admin_user):
        """学校削除時にAPIキーも削除される"""
        storage.save_api_key(
            school_id=test_school.id,
            provider="gemini",
            api_key="test-key-12345678",
            created_by=admin_user.id,
        )
        keys = storage.list_api_keys(test_school.id)
        assert len(keys) == 1

        summary = storage.delete_school_data(test_school.id, user_id=admin_user.id)
        assert summary["api_keys_deleted"] == 1
        assert summary["school_deleted"] == 1

        # 削除後はAPIキーも取得不可
        keys_after = storage.list_api_keys(test_school.id)
        assert len(keys_after) == 0


class TestAuditLogTransaction:
    """監査ログの単一トランザクション統合"""

    def test_chain_integrity_after_rapid_inserts(self, test_db):
        """連続書き込み後もチェーンが正しい"""
        for i in range(20):
            log_audit_event(
                action=f"test_action_{i}",
                resource_type="test",
                user_id=f"user-{i}",
                school_id=f"school-{i % 3}",
                details={"index": i},
            )

        is_valid, errors = verify_audit_chain()
        assert is_valid, f"チェーン検証失敗: {errors}"


class TestVerifyAuditChainPaging:
    """verify_audit_chain のページング全件走査"""

    def test_paging_with_small_page_size(self, test_db):
        """page_size=3 で10件を走査してもチェーンが正しい"""
        for i in range(10):
            log_audit_event(
                action=f"page_test_{i}",
                resource_type="test",
                details={"i": i},
            )

        is_valid, errors = verify_audit_chain(page_size=3)
        assert is_valid, f"ページング検証失敗: {errors}"

    def test_paging_single_page(self, test_db):
        """全件が1ページに収まる場合"""
        log_audit_event(action="single", resource_type="test")
        is_valid, errors = verify_audit_chain(page_size=1000)
        assert is_valid


class TestRateLimiting:
    """login / mfa/verify のレート制限"""

    def test_login_rate_limit(self, test_db, test_user):
        """ログインは10回/分を超えると429"""
        for i in range(10):
            client.post("/api/v1/auth/login", json={
                "email": "teacher@test.example.com",
                "password": "wrongpass",
            })

        # 11回目は429
        resp = client.post("/api/v1/auth/login", json={
            "email": "teacher@test.example.com",
            "password": "testpassword",
        })
        assert resp.status_code == 429
        assert "リクエストが多すぎます" in resp.json()["detail"]

    def test_login_under_limit_succeeds(self, test_db, test_user):
        """制限内のログインは成功する"""
        resp = client.post("/api/v1/auth/login", json={
            "email": "teacher@test.example.com",
            "password": "testpassword",
        })
        assert resp.status_code == 200

    def test_mfa_verify_rate_limit(self, test_db):
        """MFA検証は5回/分を超えると429"""
        for i in range(5):
            client.post("/api/v1/auth/mfa/verify", json={
                "mfa_token": "dummy-token",
                "code": "000000",
            })

        # 6回目は429
        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": "dummy-token",
            "code": "000000",
        })
        assert resp.status_code == 429

    def test_rate_limiter_class_basic(self):
        """_InMemoryRateLimiterクラスの基本動作"""
        from api.app import _InMemoryRateLimiter

        rl = _InMemoryRateLimiter(max_requests=3, window=60)
        assert rl.check("ip1") is True
        assert rl.check("ip1") is True
        assert rl.check("ip1") is True
        assert rl.check("ip1") is False  # 4回目は拒否

        # 別IPは影響なし
        assert rl.check("ip2") is True

    def test_rate_limiter_reset(self):
        """_InMemoryRateLimiter.reset()でクリアされる"""
        from api.app import _InMemoryRateLimiter

        rl = _InMemoryRateLimiter(max_requests=1, window=60)
        assert rl.check("ip") is True
        assert rl.check("ip") is False
        rl.reset()
        assert rl.check("ip") is True

    def test_trusted_proxy_restricts_forwarded_for(self):
        """信頼済みプロキシ未設定時は X-Forwarded-For を無視する"""
        from unittest.mock import MagicMock
        from api import app as api_module

        original = api_module._TRUSTED_PROXIES
        try:
            api_module._TRUSTED_PROXIES = set()
            request = MagicMock()
            request.client.host = "192.168.1.1"
            request.headers = {"x-forwarded-for": "10.0.0.1"}
            assert api_module._get_client_ip(request) == "192.168.1.1"
        finally:
            api_module._TRUSTED_PROXIES = original

    def test_trusted_proxy_allows_forwarded_for(self):
        """信頼済みプロキシからの X-Forwarded-For は使用する"""
        from unittest.mock import MagicMock
        from api import app as api_module

        original = api_module._TRUSTED_PROXIES
        try:
            api_module._TRUSTED_PROXIES = {"192.168.1.1"}
            request = MagicMock()
            request.client.host = "192.168.1.1"
            request.headers = {"x-forwarded-for": "10.0.0.1, 192.168.1.1"}
            assert api_module._get_client_ip(request) == "10.0.0.1"
        finally:
            api_module._TRUSTED_PROXIES = original

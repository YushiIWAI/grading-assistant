"""監査ログのテスト"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage import list_audit_logs, log_audit_event, verify_audit_chain


class TestAuditLog:
    """監査ログの記録・検索・チェーン検証"""

    def test_log_and_list(self, test_db, test_school, test_user):
        """監査ログの記録と取得"""
        log_id = log_audit_event(
            action="login",
            resource_type="user",
            resource_id=test_user.id,
            user_id=test_user.id,
            school_id=test_school.id,
            details={"email": test_user.email},
        )
        assert log_id

        logs = list_audit_logs(school_id=test_school.id)
        assert len(logs) == 1
        assert logs[0]["action"] == "login"
        assert logs[0]["resource_type"] == "user"
        assert logs[0]["resource_id"] == test_user.id
        assert logs[0]["details"]["email"] == test_user.email

    def test_chain_integrity(self, test_db):
        """HMACチェーンの整合性検証"""
        log_audit_event(action="test1", resource_type="test")
        log_audit_event(action="test2", resource_type="test")
        log_audit_event(action="test3", resource_type="test")

        is_valid, errors = verify_audit_chain()
        assert is_valid
        assert errors == []

    def test_filter_by_action(self, test_db):
        """アクション別のフィルタ"""
        log_audit_event(action="login", resource_type="user")
        log_audit_event(action="create", resource_type="session")
        log_audit_event(action="login", resource_type="user")

        logs = list_audit_logs(action="login")
        assert len(logs) == 2
        assert all(log["action"] == "login" for log in logs)

    def test_filter_by_resource(self, test_db):
        """リソース別のフィルタ"""
        log_audit_event(action="create", resource_type="session", resource_id="s1")
        log_audit_event(action="update", resource_type="session", resource_id="s1")
        log_audit_event(action="create", resource_type="user", resource_id="u1")

        logs = list_audit_logs(resource_type="session", resource_id="s1")
        assert len(logs) == 2

    def test_pagination(self, test_db):
        """ページネーション"""
        for i in range(5):
            log_audit_event(action=f"test{i}", resource_type="test")

        page1 = list_audit_logs(limit=2, offset=0)
        page2 = list_audit_logs(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]

    def test_empty_chain_is_valid(self, test_db):
        """空チェーンは有効"""
        is_valid, errors = verify_audit_chain()
        assert is_valid
        assert errors == []

    def test_prev_hash_chain(self, test_db):
        """prev_hashがチェーンを形成する"""
        log_audit_event(action="first", resource_type="test")
        log_audit_event(action="second", resource_type="test")

        logs = list_audit_logs()
        # list_audit_logsはdesc順なので逆順
        assert logs[0]["prev_hash"] != ""
        assert logs[1]["prev_hash"] == ""  # 最初のログ

"""Codexレビュー第2回修正のテスト

1. 管理者API越権防止（_require_admin_for_school）
2. 暗号化ON時の list_sessions / export_school_data 復号
3. 復号失敗時の明示的エラー（DecryptionError）
4. audit_logs 匿名化
5. バックアップコード3形式互換照合
6. token_invalidated_at によるトークン失効
7. verify_audit_chain のグローバルチェーン検証
8. purge_expired_sessions の school_id スコープ
9. 匿名化後もHMACチェーンが壊れないことの検証
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
import encryption
import storage
from auth import (
    create_access_token,
    generate_backup_codes,
    generate_mfa_secret,
    hash_password,
    verify_backup_code,
)
from models import School, ScoringSession, User
from storage import DecryptionError


class TestAdminSchoolScope:
    """管理者は自校のみ操作可能、他校は403"""

    def test_purge_scoped_to_school(self, test_db, test_school):
        """purge_expired_sessions(school_id=) は対象学校のみパージ"""
        # retention_days=0 にして即期限切れにする
        other_school = School(name="他校", slug="other", retention_days=0)
        storage.create_school(other_school)
        # test_school も retention_days=0 に更新
        engine = db_module.get_engine()
        with engine.begin() as conn:
            conn.execute(
                db_module.schools.update()
                .where(db_module.schools.c.id == test_school.id)
                .values(retention_days=0)
            )

        # 両校にセッションを作る
        for sid, school in [("s1", test_school), ("s2", other_school)]:
            session = ScoringSession(session_id=sid, rubric_title="test")
            storage.save_session(session, school_id=school.id)

        # save_session が updated_at を現在時刻にするので、DBを直接古い日時に更新
        with engine.begin() as conn:
            conn.execute(
                db_module.scoring_sessions.update()
                .where(db_module.scoring_sessions.c.session_id.in_(["s1", "s2"]))
                .values(updated_at="2020-01-01T00:00:00")
            )

        # test_school のみパージ
        purged = storage.purge_expired_sessions(school_id=test_school.id)
        assert "s1" in purged
        assert "s2" not in purged

        # other_school のセッションは残っている
        assert storage.load_session("s2", school_id=other_school.id) is not None

    def test_verify_audit_chain_global(self, test_db):
        """verify_audit_chain はグローバルチェーン検証（複数校混在OK）"""
        storage.log_audit_event(
            action="test_a", resource_type="test", school_id="school-a"
        )
        storage.log_audit_event(
            action="test_b", resource_type="test", school_id="school-b"
        )
        storage.log_audit_event(
            action="test_c", resource_type="test", school_id="school-a"
        )
        # 複数校が混在しても全体チェーンは正常
        is_valid, errors = storage.verify_audit_chain()
        assert is_valid
        assert errors == []


class TestEncryptedListAndExport:
    """暗号化ON時に list_sessions / export_school_data が正しくデータを返す"""

    def test_list_sessions_with_encryption(self, test_db, test_school):
        """暗号化ONでもlist_sessionsがstudent_countを正しく返す"""
        from cryptography.fernet import Fernet

        session = ScoringSession(session_id="enc1", rubric_title="暗号テスト")
        session.students = [
            {"student_id": "1", "student_name": "生徒1"},
            {"student_id": "2", "student_name": "生徒2"},
        ]

        key = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.save_session(session, school_id=test_school.id)

            sessions = storage.list_sessions(school_id=test_school.id)
            assert len(sessions) == 1
            assert sessions[0]["student_count"] == 2

        encryption._fernet = None

    def test_export_school_data_with_encryption(self, test_db, test_school):
        """暗号化ONでもexport_school_dataがデータを返す"""
        from cryptography.fernet import Fernet

        session = ScoringSession(session_id="enc2", rubric_title="エクスポートテスト")
        session.students = [{"student_id": "1", "student_name": "生徒1"}]

        key = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.save_session(session, school_id=test_school.id)

            data = storage.export_school_data(test_school.id)
            assert len(data["sessions"]) == 1
            assert len(data["sessions"][0]["students"]) == 1

        encryption._fernet = None


class TestDecryptionError:
    """暗号化カラムがあるのに復号できない場合はDecryptionError"""

    def test_wrong_key_raises_error(self, test_db, test_school):
        """鍵不一致時にDecryptionErrorが発生する"""
        from cryptography.fernet import Fernet

        session = ScoringSession(session_id="dec1", rubric_title="復号テスト")
        session.students = [{"student_id": "1"}]

        # 正しい鍵で保存
        key1 = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key1):
            encryption._fernet = None
            storage.save_session(session, school_id=test_school.id)

        # 別の鍵で読み込み → DecryptionError
        key2 = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key2):
            encryption._fernet = None
            with pytest.raises(DecryptionError):
                storage.load_session("dec1", school_id=test_school.id)

        encryption._fernet = None

    def test_unencrypted_data_falls_back_to_plaintext(self, test_db, test_school):
        """暗号化カラムがNULLの旧データは平文フォールバック"""
        session = ScoringSession(session_id="dec2", rubric_title="旧データテスト")
        session.students = [{"student_id": "1"}]

        # 暗号化無効で保存
        with patch.object(encryption, "_ENCRYPTION_KEY", None):
            encryption._fernet = None
            storage.save_session(session, school_id=test_school.id)

            # 読み込みOK（平文フォールバック）
            loaded = storage.load_session("dec2", school_id=test_school.id)
            assert len(loaded.students) == 1

        encryption._fernet = None


class TestAuditLogAnonymization:
    """delete_school_data で監査ログが匿名化される"""

    def test_audit_logs_anonymized_on_delete(self, test_db, test_school, admin_user):
        """学校削除後、監査ログの個人情報が匿名化される"""
        storage.log_audit_event(
            action="test_action",
            resource_type="session",
            user_id=admin_user.id,
            school_id=test_school.id,
            details={"email": "test@example.com"},
            ip_address="192.168.1.1",
        )

        summary = storage.delete_school_data(test_school.id, user_id=admin_user.id)
        assert "audit_logs_anonymized" in summary
        assert summary["audit_logs_anonymized"] >= 1

        # 匿名化されたログを確認
        engine = db_module.get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(db_module.audit_logs).where(
                    db_module.audit_logs.c.school_id == test_school.id
                )
            ).mappings().all()

        # 匿名化前のログ（delete_school_data の監査ログ以外）
        anonymized = [r for r in rows if r["action"] == "test_action"]
        for row in anonymized:
            assert row["user_id"] is None
            assert row["details"] is None
            assert row["ip_address"] is None

    def test_anonymization_does_not_break_hmac_chain(self, test_db, test_school, admin_user):
        """匿名化後もHMACチェーンが壊れない（v2: PII除外署名）"""
        # PII付きのログを複数作成
        storage.log_audit_event(
            action="action1", resource_type="session",
            user_id=admin_user.id, school_id=test_school.id,
            details={"email": "test@example.com"}, ip_address="10.0.0.1",
        )
        storage.log_audit_event(
            action="action2", resource_type="session",
            user_id=admin_user.id, school_id=test_school.id,
            details={"key": "value"}, ip_address="10.0.0.2",
        )

        # 匿名化前: チェーンは正常
        is_valid, errors = storage.verify_audit_chain()
        assert is_valid, f"匿名化前にチェーンが壊れている: {errors}"

        # 匿名化実行
        storage.delete_school_data(test_school.id, user_id=admin_user.id)

        # 匿名化後: チェーンは依然正常（v2ではPIIは署名対象外）
        is_valid, errors = storage.verify_audit_chain()
        assert is_valid, f"匿名化後にチェーンが壊れた: {errors}"


class TestBackupCode3FormatCompat:
    """バックアップコード3形式互換（bcrypt / SHA-256 / 平文）"""

    def test_bcrypt_format(self):
        """新形式: bcryptハッシュとの照合"""
        from auth import hash_backup_code
        code = "abcdef0123456789"
        hashed = hash_backup_code(code)
        codes_json = json.dumps([hashed, "other"])
        valid, updated = verify_backup_code(codes_json, code)
        assert valid is True
        assert json.loads(updated) == ["other"]

    def test_sha256_legacy_format(self):
        """旧形式: SHA-256ハッシュとの照合"""
        import hashlib
        code = "abcdef0123456789"
        sha_hash = hashlib.sha256(code.encode()).hexdigest()
        codes_json = json.dumps([sha_hash])
        valid, updated = verify_backup_code(codes_json, code)
        assert valid is True

    def test_plaintext_legacy_format(self):
        """最旧形式: 平文との照合"""
        code = "abcd1234"
        codes_json = json.dumps([code, "other"])
        valid, updated = verify_backup_code(codes_json, code)
        assert valid is True

    def test_invalid_code(self):
        """無効なコードは拒否"""
        from auth import hash_backup_code
        hashed = hash_backup_code("correct_code_1234")
        codes_json = json.dumps([hashed])
        valid, _ = verify_backup_code(codes_json, "wrong_code_0000")
        assert valid is False


class TestTokenInvalidation:
    """token_invalidated_at によるトークン失効"""

    def test_old_token_rejected_after_invalidation(self, test_db, test_school):
        """失効タイムスタンプより前のトークンは拒否される"""
        from fastapi.testclient import TestClient
        from api.app import app

        user = User(
            school_id=test_school.id,
            email="invalidate@test.com",
            hashed_password=hash_password("pass"),
            display_name="テスト",
            role="teacher",
            # 未来の時刻で全トークンを失効
            token_invalidated_at=datetime.now(timezone.utc).isoformat(),
        )
        storage.create_user(user)

        # 少し前の iat でトークンを作成（失効前に発行されたトークンをシミュレート）
        import jwt as pyjwt
        import auth
        old_iat = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        token = pyjwt.encode(
            {
                "sub": user.id,
                "school_id": user.school_id,
                "role": user.role,
                "type": "access",
                "iat": old_iat,
                "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
            },
            auth.JWT_SECRET_KEY,
            algorithm="HS256",
        )

        client = TestClient(app)
        resp = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        assert "失効" in resp.json()["detail"]


# ===== 第4回レビュー修正テスト =====


class TestMfaBackupCodesEncryption:
    """Warning1: MFAバックアップコードが暗号化ON時に正しく保存・復号されること"""

    def test_enable_mfa_encrypts_backup_codes(self, test_db, test_school, monkeypatch):
        """暗号化ON時、enable_mfa() のバックアップコードが暗号化保存され、
        get_user() で正しく復号されること（503にならない）。
        """
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        # モジュールレベルキャッシュも差し替えて暗号化経路を確実に通す
        monkeypatch.setattr(encryption, "_ENCRYPTION_KEY", key)
        monkeypatch.setattr(encryption, "_fernet", None)

        user = User(
            school_id=test_school.id,
            email="mfa-enc@test.example.com",
            hashed_password="dummy",
            display_name="MFA暗号化テスト",
            role="teacher",
        )
        storage.create_user(user)

        # 暗号化が有効であることを確認
        assert encryption.is_encryption_enabled()

        # MFA setup + enable
        secret = generate_mfa_secret()
        storage.setup_mfa(user.id, secret)
        backup_codes = storage.enable_mfa(user.id)
        assert backup_codes is not None

        # get_user() が DecryptionError を投げずに返ること
        fetched = storage.get_user(user.id)
        assert fetched is not None
        assert fetched.mfa_enabled is True

        # バックアップコードが復号された JSON として読めること
        codes_data = json.loads(fetched.mfa_backup_codes)
        assert len(codes_data) == len(backup_codes)

    def test_update_mfa_backup_codes_encrypts(self, test_db, test_school, monkeypatch):
        """暗号化ON時、update_mfa_backup_codes() も暗号化保存されること。"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr(encryption, "_ENCRYPTION_KEY", key)
        monkeypatch.setattr(encryption, "_fernet", None)

        user = User(
            school_id=test_school.id,
            email="mfa-update@test.example.com",
            hashed_password="dummy",
            display_name="MFA更新テスト",
            role="teacher",
        )
        storage.create_user(user)
        storage.setup_mfa(user.id, generate_mfa_secret())
        storage.enable_mfa(user.id)

        # コードを更新
        new_codes = ["hash1", "hash2"]
        storage.update_mfa_backup_codes(user.id, json.dumps(new_codes))

        # 復号して読めること
        fetched = storage.get_user(user.id)
        assert json.loads(fetched.mfa_backup_codes) == new_codes


class TestValidateSecretsKeyFormat:
    """Warning3: validate_secrets() がFernet鍵形式を検証すること"""

    def test_invalid_fernet_key_rejected_in_production(self, monkeypatch):
        """本番環境で不正な ENCRYPTION_KEY が ConfigurationError を投げること。"""
        import config as config_module

        monkeypatch.setattr(config_module, "APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "strong-production-secret-key-12345")
        monkeypatch.setenv("AUDIT_HMAC_KEY", "strong-audit-hmac-key-12345")
        monkeypatch.setenv("ENCRYPTION_KEY", "not-a-valid-fernet-key")

        with pytest.raises(config_module.ConfigurationError, match="ENCRYPTION_KEY"):
            config_module.validate_secrets()

    def test_valid_fernet_key_accepted(self, monkeypatch):
        """正しい Fernet 鍵は通ること。"""
        from cryptography.fernet import Fernet
        import config as config_module

        monkeypatch.setattr(config_module, "APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "strong-production-secret-key-12345")
        monkeypatch.setenv("AUDIT_HMAC_KEY", "strong-audit-hmac-key-12345")
        monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

        # 例外なく通る
        config_module.validate_secrets()


class TestPurgeExportAuditUserId:
    """Warning5: purge/export の監査ログに user_id が記録されること"""

    def test_purge_logs_user_id(self, test_db, test_school):
        """purge_expired_sessions() の監査ログに user_id が含まれること。"""
        # retention_days=0 にして即期限切れ
        engine = db_module.get_engine()
        with engine.begin() as conn:
            conn.execute(
                db_module.schools.update()
                .where(db_module.schools.c.id == test_school.id)
                .values(retention_days=0)
            )

        session = ScoringSession(session_id="purge-test", rubric_title="test")
        storage.save_session(session, school_id=test_school.id)

        # updated_at を古い日時に
        with engine.begin() as conn:
            conn.execute(
                db_module.scoring_sessions.update()
                .where(db_module.scoring_sessions.c.session_id == "purge-test")
                .values(updated_at="2020-01-01T00:00:00")
            )

        storage.purge_expired_sessions(school_id=test_school.id, user_id="admin-user-123")

        # 監査ログに user_id が記録されていること
        logs = storage.list_audit_logs(action="purge_expired")
        assert len(logs) >= 1
        assert logs[0]["user_id"] == "admin-user-123"

    def test_export_logs_user_id(self, test_db, test_school):
        """export_school_data() の監査ログに user_id が含まれること。"""
        storage.export_school_data(test_school.id, user_id="admin-user-456")

        logs = storage.list_audit_logs(action="export_school_data")
        assert len(logs) >= 1
        assert logs[0]["user_id"] == "admin-user-456"


# ===== Phase2: token_invalidated_at セット処理テスト =====


class TestTokenInvalidationOnChange:
    """パスワード変更・MFA変更時にtoken_invalidated_atがセットされること"""

    def test_change_password_invalidates_tokens(self, test_db, test_school):
        """change_password() 後に token_invalidated_at がセットされる"""
        user = User(
            school_id=test_school.id,
            email="pw-change@test.com",
            hashed_password=hash_password("old_password"),
            display_name="テスト",
            role="teacher",
        )
        storage.create_user(user)
        assert storage.get_user(user.id).token_invalidated_at is None

        storage.change_password(user.id, hash_password("new_password"))
        updated = storage.get_user(user.id)
        assert updated.token_invalidated_at is not None

    def test_enable_mfa_invalidates_tokens(self, test_db, test_school):
        """enable_mfa() 後に token_invalidated_at がセットされる"""
        user = User(
            school_id=test_school.id,
            email="mfa-enable-inv@test.com",
            hashed_password="dummy",
            display_name="テスト",
            role="teacher",
            mfa_secret=generate_mfa_secret(),
        )
        storage.create_user(user)
        assert storage.get_user(user.id).token_invalidated_at is None

        storage.enable_mfa(user.id)
        updated = storage.get_user(user.id)
        assert updated.token_invalidated_at is not None

    def test_disable_mfa_invalidates_tokens(self, test_db, test_school):
        """disable_mfa() 後に token_invalidated_at がセットされる"""
        user = User(
            school_id=test_school.id,
            email="mfa-disable-inv@test.com",
            hashed_password="dummy",
            display_name="テスト",
            role="teacher",
            mfa_enabled=True,
            mfa_secret=generate_mfa_secret(),
        )
        storage.create_user(user)

        storage.disable_mfa(user.id)
        updated = storage.get_user(user.id)
        assert updated.token_invalidated_at is not None

    def test_change_password_api_endpoint(self, test_db, test_school):
        """POST /api/v1/auth/change-password が正しく動作する"""
        from fastapi.testclient import TestClient
        from api.app import app

        user = User(
            school_id=test_school.id,
            email="pw-api@test.com",
            hashed_password=hash_password("current_pass"),
            display_name="テスト",
            role="teacher",
        )
        storage.create_user(user)

        token = create_access_token(user.id, user.school_id, user.role)
        client = TestClient(app)

        # 正しいパスワードで変更
        resp = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "current_pass", "new_password": "new_secure_pass"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "変更しました" in resp.json()["message"]

        # 監査ログが記録されていること
        logs = storage.list_audit_logs(action="change_password")
        assert len(logs) >= 1
        assert logs[0]["user_id"] == user.id

    def test_change_password_wrong_current(self, test_db, test_school):
        """現在のパスワードが間違っている場合は401"""
        from fastapi.testclient import TestClient
        from api.app import app

        user = User(
            school_id=test_school.id,
            email="pw-wrong@test.com",
            hashed_password=hash_password("correct_pass"),
            display_name="テスト",
            role="teacher",
        )
        storage.create_user(user)

        token = create_access_token(user.id, user.school_id, user.role)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong_pass", "new_password": "new_secure_pass"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


# ===== Phase2: Refresh Token Family テスト =====


class TestRefreshTokenFamily:
    """リフレッシュトークンファミリー（jti + rotate + revoke）"""

    def test_refresh_rotates_token(self, test_db, test_school):
        """refresh で新しいトークンが返り、旧トークンは使えなくなる"""
        from fastapi.testclient import TestClient
        from api.app import app

        user = User(
            school_id=test_school.id,
            email="refresh-rotate@test.com",
            hashed_password=hash_password("pass"),
            display_name="テスト",
            role="teacher",
        )
        storage.create_user(user)

        client = TestClient(app)
        # ログインでトークン取得
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "refresh-rotate@test.com", "password": "pass",
        })
        assert login_resp.status_code == 200
        refresh_token_1 = login_resp.json()["refresh_token"]

        # リフレッシュで新トークン取得
        refresh_resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token_1,
        })
        assert refresh_resp.status_code == 200
        assert "refresh_token" in refresh_resp.json()
        refresh_token_2 = refresh_resp.json()["refresh_token"]
        assert refresh_token_2 != refresh_token_1

        # 旧トークンは再利用不可（revoke 済み → ファミリー全体 revoke）
        reuse_resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token_1,
        })
        assert reuse_resp.status_code == 401

    def test_reuse_detection_revokes_family(self, test_db, test_school):
        """revoke済みトークンの再利用でファミリー全体が無効化される"""
        from fastapi.testclient import TestClient
        from api.app import app

        user = User(
            school_id=test_school.id,
            email="refresh-reuse@test.com",
            hashed_password=hash_password("pass"),
            display_name="テスト",
            role="teacher",
        )
        storage.create_user(user)

        client = TestClient(app)
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "refresh-reuse@test.com", "password": "pass",
        })
        refresh_token_1 = login_resp.json()["refresh_token"]

        # 正常ローテーション
        refresh_resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token_1,
        })
        refresh_token_2 = refresh_resp.json()["refresh_token"]

        # 旧トークン再利用（盗難シミュレート）→ ファミリー全体revoke
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token_1})

        # 新トークンも無効化されている
        resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token_2,
        })
        assert resp.status_code == 401

    def test_store_and_use_refresh_token(self, test_db, test_school):
        """storage レベルで store/use/revoke が正しく動作する"""
        storage.store_refresh_token(
            jti="test-jti-1", user_id="u1", family_id="fam-1",
            expires_at="2099-01-01T00:00:00",
        )

        # 初回使用: 成功
        result = storage.use_refresh_token("test-jti-1")
        assert result is not None
        assert result["jti"] == "test-jti-1"

        # 2回目使用: ファミリー全体 revoke → None
        result2 = storage.use_refresh_token("test-jti-1")
        assert result2 is None

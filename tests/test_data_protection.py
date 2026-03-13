"""Step 2: データ保護の実効化に関するテスト

1. save_session: 暗号化有効時に平文カラムが空値になる
2. MFAシークレットの暗号化保存・復号読み込み
3. バックアップコードのハッシュ化保存・照合
4. 監査ハッシュの署名対象拡張
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_module
import encryption
import storage
from auth import (
    generate_backup_codes,
    generate_mfa_secret,
    hash_backup_code,
    hash_backup_codes,
    verify_backup_code,
)
from models import ScoringSession
from storage import (
    _compute_audit_hash,
    log_audit_event,
    verify_audit_chain,
)


class TestSaveSessionEncryption:
    """save_session: 暗号化有効時に平文カラムが空値になる"""

    def test_plaintext_columns_empty_when_encrypted(self, test_db, sample_session):
        """暗号化有効時、平文カラム（students, ocr_results）は空リストになる"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.save_session(sample_session)

        # DBから直接読み取って平文カラムを確認
        engine = db_module.get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    db_module.scoring_sessions.c.students,
                    db_module.scoring_sessions.c.ocr_results,
                    db_module.scoring_sessions.c.students_encrypted,
                    db_module.scoring_sessions.c.ocr_results_encrypted,
                ).where(
                    db_module.scoring_sessions.c.session_id == sample_session.session_id
                )
            ).fetchone()

        # 平文カラムは空リスト
        students_plain = row[0]
        if isinstance(students_plain, str):
            students_plain = json.loads(students_plain)
        assert students_plain == []

        ocr_plain = row[1]
        if isinstance(ocr_plain, str):
            ocr_plain = json.loads(ocr_plain)
        assert ocr_plain == []

        # 暗号化カラムにはデータがある
        assert row[2] is not None and row[2] != ""
        assert row[3] is not None and row[3] != ""

    def test_plaintext_columns_filled_when_no_encryption(self, test_db, sample_session):
        """暗号化無効時、平文カラムにデータが入る"""
        with patch.object(encryption, "_ENCRYPTION_KEY", ""):
            encryption._fernet = None
            storage.save_session(sample_session)

        engine = db_module.get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    db_module.scoring_sessions.c.students,
                    db_module.scoring_sessions.c.students_encrypted,
                ).where(
                    db_module.scoring_sessions.c.session_id == sample_session.session_id
                )
            ).fetchone()

        students_plain = row[0]
        if isinstance(students_plain, str):
            students_plain = json.loads(students_plain)
        assert len(students_plain) > 0  # 平文にデータあり
        assert row[1] is None  # 暗号化カラムはNone

    def test_load_session_decrypts_correctly(self, test_db, sample_session):
        """暗号化保存→復号読み込みで元データが復元される"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.save_session(sample_session)
            loaded = storage.load_session(sample_session.session_id)

        assert loaded is not None
        assert len(loaded.students) == len(sample_session.students)
        assert loaded.students[0].student_name == sample_session.students[0].student_name


class TestMfaSecretEncryption:
    """MFAシークレットの暗号化保存"""

    def test_mfa_secret_encrypted_and_decrypted(self, test_db, test_user):
        """暗号化有効時、MFAシークレットは暗号化保存→復号読み込み"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        secret = generate_mfa_secret()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.setup_mfa(test_user.id, secret)

            # DBから直接読み取って暗号化されているか確認
            engine = db_module.get_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    sa.select(db_module.users.c.mfa_secret).where(
                        db_module.users.c.id == test_user.id
                    )
                ).fetchone()
            raw_value = row[0]
            assert raw_value != secret  # 平文ではない

            # get_userで復号されて返る
            user = storage.get_user(test_user.id)
            assert user.mfa_secret == secret

    def test_mfa_secret_plaintext_without_encryption(self, test_db, test_user):
        """暗号化無効時、MFAシークレットは平文保存"""
        secret = generate_mfa_secret()

        with patch.object(encryption, "_ENCRYPTION_KEY", ""):
            encryption._fernet = None
            storage.setup_mfa(test_user.id, secret)

            user = storage.get_user(test_user.id)
            assert user.mfa_secret == secret

    def test_get_user_by_email_decrypts_mfa(self, test_db, test_user):
        """get_user_by_emailでもMFAシークレットが復号される"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        secret = generate_mfa_secret()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            storage.setup_mfa(test_user.id, secret)

            user = storage.get_user_by_email(test_user.email)
            assert user.mfa_secret == secret


class TestBackupCodeHashing:
    """バックアップコードのハッシュ化"""

    def test_hash_backup_code_deterministic(self):
        """同じコードは同じハッシュになる"""
        code = "abcd1234"
        assert hash_backup_code(code) == hash_backup_code(code)

    def test_hash_backup_code_different(self):
        """異なるコードは異なるハッシュになる"""
        assert hash_backup_code("abcd1234") != hash_backup_code("efgh5678")

    def test_hash_backup_codes_list(self):
        """リスト全体のハッシュ化"""
        codes = generate_backup_codes()
        hashed = hash_backup_codes(codes)
        assert len(hashed) == len(codes)
        assert all(h != c for h, c in zip(hashed, codes))

    def test_verify_hashed_backup_code(self):
        """ハッシュ化済みコードの照合"""
        codes = generate_backup_codes()
        hashed = hash_backup_codes(codes)
        stored_json = json.dumps(hashed)

        # 正しいコードで照合成功
        valid, updated = verify_backup_code(stored_json, codes[0])
        assert valid is True
        remaining = json.loads(updated)
        assert len(remaining) == len(codes) - 1

    def test_verify_wrong_code_fails(self):
        """間違ったコードは照合失敗"""
        codes = generate_backup_codes()
        hashed = hash_backup_codes(codes)
        stored_json = json.dumps(hashed)

        valid, updated = verify_backup_code(stored_json, "wrongcode")
        assert valid is False
        assert updated == stored_json

    def test_verify_plaintext_backward_compat(self):
        """旧形式（平文保存）のバックアップコードとも照合できる"""
        codes = ["aabbccdd", "11223344"]
        stored_json = json.dumps(codes)

        valid, updated = verify_backup_code(stored_json, "aabbccdd")
        assert valid is True
        remaining = json.loads(updated)
        assert remaining == ["11223344"]

    def test_enable_mfa_stores_hashed_codes(self, test_db, test_user):
        """enable_mfaがハッシュ化コードをDBに保存する"""
        storage.setup_mfa(test_user.id, generate_mfa_secret())
        codes = storage.enable_mfa(test_user.id)
        assert codes is not None

        user = storage.get_user(test_user.id)
        stored = json.loads(user.mfa_backup_codes)
        # DBにはハッシュが入っている
        assert stored == hash_backup_codes(codes)
        # 平文とは一致しない
        assert stored != codes


class TestAuditHashExtended:
    """監査ハッシュの署名対象拡張"""

    def test_hash_includes_user_and_school(self):
        """user_id, school_idが署名に含まれる"""
        base = _compute_audit_hash(
            "id1", "2026-01-01T00:00:00", "login", "user", "u1", "",
        )
        with_user = _compute_audit_hash(
            "id1", "2026-01-01T00:00:00", "login", "user", "u1", "",
            user_id="user-123",
        )
        with_school = _compute_audit_hash(
            "id1", "2026-01-01T00:00:00", "login", "user", "u1", "",
            school_id="school-456",
        )
        assert base != with_user
        assert base != with_school
        assert with_user != with_school

    def test_hash_includes_details(self):
        """detailsが署名に含まれる"""
        h1 = _compute_audit_hash(
            "id1", "2026-01-01T00:00:00", "login", "user", None, "",
            details={"email": "a@b.com"},
        )
        h2 = _compute_audit_hash(
            "id1", "2026-01-01T00:00:00", "login", "user", None, "",
            details={"email": "x@y.com"},
        )
        assert h1 != h2

    def test_hash_details_key_order_independent(self):
        """detailsのキー順序が異なっても同じハッシュになる"""
        h1 = _compute_audit_hash(
            "id1", "ts", "a", "t", None, "",
            details={"a": 1, "b": 2},
        )
        h2 = _compute_audit_hash(
            "id1", "ts", "a", "t", None, "",
            details={"b": 2, "a": 1},
        )
        assert h1 == h2

    def test_hash_includes_ip_address(self):
        """ip_addressが署名に含まれる"""
        h1 = _compute_audit_hash(
            "id1", "ts", "a", "t", None, "",
        )
        h2 = _compute_audit_hash(
            "id1", "ts", "a", "t", None, "",
            ip_address="192.168.1.1",
        )
        assert h1 != h2

    def test_chain_integrity_with_extended_fields(self, test_db):
        """拡張フィールド付きの監査ログでチェーン検証が通る"""
        log_audit_event(
            action="login",
            resource_type="user",
            resource_id="u1",
            user_id="user-1",
            school_id="school-1",
            details={"email": "test@example.com"},
            ip_address="10.0.0.1",
        )
        log_audit_event(
            action="create",
            resource_type="session",
            resource_id="s1",
            user_id="user-1",
            school_id="school-1",
            details={"rubric_title": "テスト"},
        )
        log_audit_event(
            action="export",
            resource_type="session",
            resource_id="s1",
            user_id="user-2",
            school_id="school-1",
        )

        is_valid, errors = verify_audit_chain()
        assert is_valid, f"チェーン検証失敗: {errors}"
        assert errors == []

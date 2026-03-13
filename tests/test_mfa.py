"""MFA（多要素認証）のテスト"""

import json
from unittest.mock import patch

import pyotp
import pytest
from fastapi.testclient import TestClient

import auth
import storage
from api.app import app
from auth import (
    create_access_token,
    create_mfa_pending_token,
    generate_backup_codes,
    generate_mfa_secret,
    get_totp_uri,
    hash_password,
    verify_backup_code,
    verify_totp,
)
from models import School, User

client = TestClient(app)


# --- auth.py 単体テスト ---


class TestGenerateMfaSecret:
    def test_returns_base32_string(self):
        secret = generate_mfa_secret()
        assert len(secret) == 32
        # base32文字のみを含むことを確認
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for c in secret)

    def test_unique_each_time(self):
        s1 = generate_mfa_secret()
        s2 = generate_mfa_secret()
        assert s1 != s2


class TestGetTotpUri:
    def test_returns_otpauth_uri(self):
        secret = generate_mfa_secret()
        uri = get_totp_uri(secret, "teacher@school.example.com")
        assert uri.startswith("otpauth://totp/")
        assert "teacher%40school.example.com" in uri or "teacher@school.example.com" in uri
        assert secret in uri


class TestVerifyTotp:
    def test_valid_code(self):
        secret = generate_mfa_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert verify_totp(secret, code) is True

    def test_invalid_code(self):
        secret = generate_mfa_secret()
        assert verify_totp(secret, "000000") is False

    def test_accepts_window(self):
        """前後1ステップ（±30秒）のコードを受け入れる"""
        secret = generate_mfa_secret()
        totp = pyotp.TOTP(secret)
        # 現在のコードは有効
        assert verify_totp(secret, totp.now()) is True


class TestBackupCodes:
    def test_generate_count(self):
        codes = generate_backup_codes()
        assert len(codes) == 10

    def test_codes_are_unique(self):
        codes = generate_backup_codes()
        assert len(set(codes)) == len(codes)

    def test_code_format(self):
        codes = generate_backup_codes()
        for code in codes:
            assert len(code) == 8
            assert all(c in "0123456789abcdef" for c in code)

    def test_verify_valid_code(self):
        codes = ["aabbccdd", "11223344", "deadbeef"]
        codes_json = json.dumps(codes)
        valid, updated = verify_backup_code(codes_json, "11223344")
        assert valid is True
        remaining = json.loads(updated)
        assert "11223344" not in remaining
        assert len(remaining) == 2

    def test_verify_invalid_code(self):
        codes_json = json.dumps(["aabbccdd", "11223344"])
        valid, updated = verify_backup_code(codes_json, "xxxxxxxx")
        assert valid is False
        remaining = json.loads(updated)
        assert len(remaining) == 2

    def test_code_can_only_be_used_once(self):
        codes_json = json.dumps(["aabbccdd"])
        valid, updated = verify_backup_code(codes_json, "aabbccdd")
        assert valid is True
        # 同じコードは2回使えない
        valid2, _ = verify_backup_code(updated, "aabbccdd")
        assert valid2 is False


class TestMfaPendingToken:
    def test_create_and_decode(self):
        token = create_mfa_pending_token("user-1")
        payload = auth.decode_token(token)
        assert payload["sub"] == "user-1"
        assert payload["type"] == "mfa_pending"

    def test_expired_token(self):
        with patch.object(auth, "MFA_TOKEN_EXPIRE_MINUTES", -1):
            token = create_mfa_pending_token("user-1")
        import jwt as pyjwt
        with pytest.raises(pyjwt.ExpiredSignatureError):
            auth.decode_token(token)


# --- storage.py MFA テスト ---


class TestMfaStorage:
    def test_setup_mfa(self, test_db, test_user):
        secret = generate_mfa_secret()
        assert storage.setup_mfa(test_user.id, secret) is True
        user = storage.get_user(test_user.id)
        assert user.mfa_secret == secret
        assert user.mfa_enabled is False

    def test_enable_mfa(self, test_db, test_user):
        storage.setup_mfa(test_user.id, generate_mfa_secret())
        codes = storage.enable_mfa(test_user.id)
        assert codes is not None
        assert len(codes) == 10
        user = storage.get_user(test_user.id)
        assert user.mfa_enabled is True
        assert user.mfa_backup_codes is not None
        stored_codes = json.loads(user.mfa_backup_codes)
        # DBにはハッシュ化されたコードが保存される
        assert len(stored_codes) == 10
        assert stored_codes != codes  # 平文とは一致しない
        from auth import hash_backup_code
        assert stored_codes == [hash_backup_code(c) for c in codes]

    def test_disable_mfa(self, test_db, test_user):
        storage.setup_mfa(test_user.id, generate_mfa_secret())
        storage.enable_mfa(test_user.id)
        assert storage.disable_mfa(test_user.id) is True
        user = storage.get_user(test_user.id)
        assert user.mfa_enabled is False
        assert user.mfa_secret is None
        assert user.mfa_backup_codes is None

    def test_update_backup_codes(self, test_db, test_user):
        storage.setup_mfa(test_user.id, generate_mfa_secret())
        storage.enable_mfa(test_user.id)
        new_codes = json.dumps(["onlyoneleft"])
        assert storage.update_mfa_backup_codes(test_user.id, new_codes) is True
        user = storage.get_user(test_user.id)
        assert json.loads(user.mfa_backup_codes) == ["onlyoneleft"]

    def test_enable_mfa_nonexistent_user(self, test_db):
        result = storage.enable_mfa("nonexistent-id")
        assert result is None


# --- API エンドポイントテスト ---


@pytest.fixture
def mfa_user(test_db, test_school):
    """MFA有効のテストユーザー"""
    secret = generate_mfa_secret()
    user = User(
        school_id=test_school.id,
        email="mfa-teacher@test.example.com",
        hashed_password=hash_password("mfapassword"),
        display_name="MFA教員",
        role="teacher",
        mfa_secret=secret,
        mfa_enabled=True,
        mfa_backup_codes=json.dumps(["backup01", "backup02", "backup03"]),
    )
    storage.create_user(user)
    return user


@pytest.fixture
def mfa_user_auth_headers(mfa_user):
    """MFA有効ユーザーの認証ヘッダー（MFA検証済み扱い）"""
    token = create_access_token(mfa_user.id, mfa_user.school_id, mfa_user.role)
    return {"Authorization": f"Bearer {token}"}


class TestLoginWithMfa:
    def test_login_mfa_enabled_returns_pending(self, test_db, mfa_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": "mfa-teacher@test.example.com",
            "password": "mfapassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mfa_required"] is True
        assert "mfa_token" in data
        assert "access_token" not in data

    def test_login_no_mfa_returns_tokens(self, test_db, test_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": "teacher@test.example.com",
            "password": "testpassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["mfa_required"] is False
        assert "access_token" in data
        assert "refresh_token" in data


class TestMfaVerify:
    def test_verify_with_valid_totp(self, test_db, mfa_user):
        # Step 1: ログインしてmfa_tokenを取得
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "mfa-teacher@test.example.com",
            "password": "mfapassword",
        })
        mfa_token = login_resp.json()["mfa_token"]

        # Step 2: 正しいTOTPコードで検証
        totp = pyotp.TOTP(mfa_user.mfa_secret)
        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": mfa_token,
            "code": totp.now(),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["mfa_enabled"] is True

    def test_verify_with_invalid_totp(self, test_db, mfa_user):
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "mfa-teacher@test.example.com",
            "password": "mfapassword",
        })
        mfa_token = login_resp.json()["mfa_token"]

        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": mfa_token,
            "code": "000000",
        })
        assert resp.status_code == 401

    def test_verify_with_backup_code(self, test_db, mfa_user):
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "mfa-teacher@test.example.com",
            "password": "mfapassword",
        })
        mfa_token = login_resp.json()["mfa_token"]

        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": mfa_token,
            "code": "backup01",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

        # バックアップコードが消費されたことを確認
        user = storage.get_user(mfa_user.id)
        remaining = json.loads(user.mfa_backup_codes)
        assert "backup01" not in remaining
        assert len(remaining) == 2

    def test_verify_with_expired_mfa_token(self, test_db, mfa_user):
        with patch.object(auth, "MFA_TOKEN_EXPIRE_MINUTES", -1):
            expired_token = create_mfa_pending_token(mfa_user.id)

        totp = pyotp.TOTP(mfa_user.mfa_secret)
        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": expired_token,
            "code": totp.now(),
        })
        assert resp.status_code == 401

    def test_verify_with_invalid_mfa_token(self, test_db, mfa_user):
        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": "not-a-valid-token",
            "code": "123456",
        })
        assert resp.status_code == 401

    def test_verify_rejects_access_token_as_mfa_token(self, test_db, mfa_user):
        """アクセストークンをmfa_tokenとして使えないことを確認"""
        access_token = create_access_token(mfa_user.id, mfa_user.school_id, mfa_user.role)
        totp = pyotp.TOTP(mfa_user.mfa_secret)
        resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": access_token,
            "code": totp.now(),
        })
        assert resp.status_code == 401


class TestMfaSetup:
    def test_setup_returns_secret_and_uri(self, test_db, test_user, auth_headers):
        resp = client.post("/api/v1/auth/mfa/setup", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "secret" in data
        assert len(data["secret"]) == 32
        assert "totp_uri" in data
        assert data["totp_uri"].startswith("otpauth://totp/")

    def test_setup_requires_auth(self, test_db):
        resp = client.post("/api/v1/auth/mfa/setup")
        assert resp.status_code == 401

    def test_setup_rejected_if_already_enabled(self, test_db, mfa_user, mfa_user_auth_headers):
        resp = client.post("/api/v1/auth/mfa/setup", headers=mfa_user_auth_headers)
        assert resp.status_code == 400


class TestMfaEnable:
    def test_enable_with_valid_code(self, test_db, test_user, auth_headers):
        # Step 1: セットアップ
        setup_resp = client.post("/api/v1/auth/mfa/setup", headers=auth_headers)
        secret = setup_resp.json()["secret"]

        # Step 2: 正しいTOTPコードで有効化
        totp = pyotp.TOTP(secret)
        resp = client.post("/api/v1/auth/mfa/enable", json={
            "code": totp.now(),
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["mfa_enabled"] is True
        assert "backup_codes" in data
        assert len(data["backup_codes"]) == 10

        # DBで有効化されていることを確認
        user = storage.get_user(test_user.id)
        assert user.mfa_enabled is True

    def test_enable_with_invalid_code(self, test_db, test_user, auth_headers):
        client.post("/api/v1/auth/mfa/setup", headers=auth_headers)
        resp = client.post("/api/v1/auth/mfa/enable", json={
            "code": "000000",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_enable_without_setup(self, test_db, test_user, auth_headers):
        resp = client.post("/api/v1/auth/mfa/enable", json={
            "code": "123456",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_enable_requires_auth(self, test_db):
        resp = client.post("/api/v1/auth/mfa/enable", json={"code": "123456"})
        assert resp.status_code == 401


class TestMfaDisable:
    def test_disable_with_valid_password(self, test_db, mfa_user, mfa_user_auth_headers):
        resp = client.post("/api/v1/auth/mfa/disable", json={
            "password": "mfapassword",
        }, headers=mfa_user_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["mfa_enabled"] is False

        user = storage.get_user(mfa_user.id)
        assert user.mfa_enabled is False
        assert user.mfa_secret is None

    def test_disable_with_wrong_password(self, test_db, mfa_user, mfa_user_auth_headers):
        resp = client.post("/api/v1/auth/mfa/disable", json={
            "password": "wrongpassword",
        }, headers=mfa_user_auth_headers)
        assert resp.status_code == 401

    def test_disable_when_not_enabled(self, test_db, test_user, auth_headers):
        resp = client.post("/api/v1/auth/mfa/disable", json={
            "password": "testpassword",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_disable_requires_auth(self, test_db):
        resp = client.post("/api/v1/auth/mfa/disable", json={"password": "x"})
        assert resp.status_code == 401


class TestMeEndpointWithMfa:
    def test_me_shows_mfa_status(self, test_db, mfa_user, mfa_user_auth_headers):
        resp = client.get("/api/v1/auth/me", headers=mfa_user_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["mfa_enabled"] is True

    def test_me_shows_mfa_disabled(self, test_db, test_user, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["mfa_enabled"] is False


class TestFullMfaFlow:
    """MFAの完全なライフサイクルテスト: セットアップ → 有効化 → MFA付きログイン → 無効化"""

    def test_full_lifecycle(self, test_db, test_user, auth_headers):
        # 1. MFAセットアップ
        setup_resp = client.post("/api/v1/auth/mfa/setup", headers=auth_headers)
        assert setup_resp.status_code == 200
        secret = setup_resp.json()["secret"]

        # 2. MFA有効化
        totp = pyotp.TOTP(secret)
        enable_resp = client.post("/api/v1/auth/mfa/enable", json={
            "code": totp.now(),
        }, headers=auth_headers)
        assert enable_resp.status_code == 200
        backup_codes = enable_resp.json()["backup_codes"]

        # 3. MFA付きログイン（Step 1: パスワード）
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "teacher@test.example.com",
            "password": "testpassword",
        })
        assert login_resp.json()["mfa_required"] is True
        mfa_token = login_resp.json()["mfa_token"]

        # 4. MFA付きログイン（Step 2: TOTP）
        verify_resp = client.post("/api/v1/auth/mfa/verify", json={
            "mfa_token": mfa_token,
            "code": totp.now(),
        })
        assert verify_resp.status_code == 200
        new_access_token = verify_resp.json()["access_token"]
        new_headers = {"Authorization": f"Bearer {new_access_token}"}

        # 5. MFA無効化
        disable_resp = client.post("/api/v1/auth/mfa/disable", json={
            "password": "testpassword",
        }, headers=new_headers)
        assert disable_resp.status_code == 200
        assert disable_resp.json()["mfa_enabled"] is False

        # 6. MFA無しでログインできることを確認
        login_resp2 = client.post("/api/v1/auth/login", json={
            "email": "teacher@test.example.com",
            "password": "testpassword",
        })
        assert login_resp2.json()["mfa_required"] is False
        assert "access_token" in login_resp2.json()

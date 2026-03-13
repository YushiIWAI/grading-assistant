"""auth モジュールのテスト"""

import time
from unittest.mock import patch

import jwt as pyjwt
import pytest

import auth


class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "my-secure-password"
        hashed = auth.hash_password(plain)
        assert hashed != plain
        assert auth.verify_password(plain, hashed)

    def test_wrong_password(self):
        hashed = auth.hash_password("correct")
        assert not auth.verify_password("wrong", hashed)

    def test_different_hashes(self):
        """同じパスワードでもソルトが異なるため異なるハッシュが生成される"""
        h1 = auth.hash_password("same")
        h2 = auth.hash_password("same")
        assert h1 != h2
        assert auth.verify_password("same", h1)
        assert auth.verify_password("same", h2)


class TestAccessToken:
    def test_create_and_decode(self):
        token = auth.create_access_token("user-1", "school-1", "teacher")
        payload = auth.decode_token(token)
        assert payload["sub"] == "user-1"
        assert payload["school_id"] == "school-1"
        assert payload["role"] == "teacher"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_expired_token(self):
        with patch.object(auth, "ACCESS_TOKEN_EXPIRE_MINUTES", -1):
            token = auth.create_access_token("user-1", "school-1", "teacher")
        with pytest.raises(pyjwt.ExpiredSignatureError):
            auth.decode_token(token)

    def test_invalid_token(self):
        with pytest.raises(pyjwt.InvalidTokenError):
            auth.decode_token("not-a-valid-token")

    def test_wrong_secret(self):
        token = auth.create_access_token("user-1", "school-1", "teacher")
        with pytest.raises(pyjwt.InvalidSignatureError):
            pyjwt.decode(token, "wrong-secret", algorithms=["HS256"])


class TestRefreshToken:
    def test_create_and_decode(self):
        token = auth.create_refresh_token("user-1")
        payload = auth.decode_token(token)
        assert payload["sub"] == "user-1"
        assert payload["type"] == "refresh"
        assert "school_id" not in payload

    def test_expired_refresh(self):
        with patch.object(auth, "REFRESH_TOKEN_EXPIRE_DAYS", -1):
            token = auth.create_refresh_token("user-1")
        with pytest.raises(pyjwt.ExpiredSignatureError):
            auth.decode_token(token)

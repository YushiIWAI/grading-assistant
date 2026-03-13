"""認証ユーティリティ: パスワードハッシュ化、JWTトークン生成・検証、TOTP MFA"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pyotp

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
MFA_TOKEN_EXPIRE_MINUTES = int(os.environ.get("MFA_TOKEN_EXPIRE_MINUTES", "5"))
MFA_ISSUER_NAME = os.environ.get("MFA_ISSUER_NAME", "採点支援アシスタント")
MFA_BACKUP_CODE_COUNT = 10


def hash_password(plain: str) -> str:
    """平文パスワードをbcryptでハッシュ化する。"""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """平文パスワードとハッシュを照合する。"""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, school_id: str, role: str) -> str:
    """アクセストークン（JWT）を生成する。"""
    payload = {
        "sub": user_id,
        "school_id": school_id,
        "role": role,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """リフレッシュトークン（JWT）を生成する。"""
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_mfa_pending_token(user_id: str) -> str:
    """MFA検証待ちの一時トークンを生成する。パスワード認証済み・TOTP未検証。"""
    payload = {
        "sub": user_id,
        "type": "mfa_pending",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=MFA_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """JWTをデコード・検証する。失敗時は jwt.InvalidTokenError を送出。"""
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


# --- TOTP MFA ---


def generate_mfa_secret() -> str:
    """新しいTOTPシークレットを生成する（base32エンコード済み）。"""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    """認証アプリ登録用のotpauth:// URIを返す。"""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=MFA_ISSUER_NAME)


def verify_totp(secret: str, code: str) -> bool:
    """TOTPコードを検証する。前後1ステップ（±30秒）を許容。"""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def hash_backup_code(code: str) -> str:
    """バックアップコードをSHA-256でハッシュ化する。"""
    return hashlib.sha256(code.encode()).hexdigest()


def generate_backup_codes() -> list[str]:
    """MFAバックアップコード（10個）を生成する。各8文字の英数字。"""
    return [secrets.token_hex(4) for _ in range(MFA_BACKUP_CODE_COUNT)]


def hash_backup_codes(codes: list[str]) -> list[str]:
    """バックアップコードリストをハッシュ化して返す（保存用）。"""
    return [hash_backup_code(c) for c in codes]


def verify_backup_code(stored_codes_json: str, code: str) -> tuple[bool, str]:
    """バックアップコードを検証し、使用済みなら除去した新しいJSON文字列を返す。

    ハッシュ化保存と平文保存の両方に対応（移行期の後方互換）。

    Returns:
        (is_valid, updated_codes_json)
    """
    codes: list[str] = json.loads(stored_codes_json)
    code_hash = hash_backup_code(code)

    # ハッシュ化済みコードとの照合
    if code_hash in codes:
        codes.remove(code_hash)
        return True, json.dumps(codes)

    # 平文コードとの照合（旧データ互換）
    if code in codes:
        codes.remove(code)
        return True, json.dumps(codes)

    return False, stored_codes_json

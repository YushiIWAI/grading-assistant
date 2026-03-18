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

from config import _get_jwt_secret

JWT_SECRET_KEY = _get_jwt_secret()
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


def create_refresh_token(
    user_id: str,
    family_id: str | None = None,
) -> tuple[str, str, str]:
    """リフレッシュトークン（JWT）を生成する。

    Args:
        user_id: ユーザーID
        family_id: トークンファミリーID（ローテーション時に引き継ぐ）。
                   None の場合は新規ファミリーを作成。

    Returns:
        (token_string, jti, family_id)
    """
    jti = secrets.token_hex(16)
    resolved_family = family_id or secrets.token_hex(16)
    exp = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": jti,
        "family_id": resolved_family,
        "exp": exp,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, jti, resolved_family


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
    """バックアップコードをbcryptでハッシュ化する。"""
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()


def _hash_backup_code_sha256(code: str) -> str:
    """旧形式: SHA-256ハッシュ（後方互換の照合用のみ）。"""
    return hashlib.sha256(code.encode()).hexdigest()


def generate_backup_codes() -> list[str]:
    """MFAバックアップコード（10個）を生成する。各16文字のhex（2^64エントロピー）。"""
    return [secrets.token_hex(8) for _ in range(MFA_BACKUP_CODE_COUNT)]


def hash_backup_codes(codes: list[str]) -> list[str]:
    """バックアップコードリストをbcryptハッシュ化して返す（保存用）。"""
    return [hash_backup_code(c) for c in codes]


def _is_bcrypt_hash(s: str) -> bool:
    """bcryptハッシュかどうかを判定する。"""
    return s.startswith(("$2b$", "$2a$", "$2y$"))


def _is_sha256_hash(s: str) -> bool:
    """SHA-256ハッシュ（64文字hex）かどうかを判定する。"""
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def verify_backup_code(stored_codes_json: str, code: str) -> tuple[bool, str]:
    """バックアップコードを検証し、使用済みなら除去した新しいJSON文字列を返す。

    3形式に対応（移行期の後方互換）:
    1. bcryptハッシュ（$2b$...）— 新形式
    2. SHA-256ハッシュ（64文字hex）— 旧形式
    3. 平文 — 最旧形式

    Returns:
        (is_valid, updated_codes_json)
    """
    codes: list[str] = json.loads(stored_codes_json)

    for i, stored in enumerate(codes):
        if _is_bcrypt_hash(stored):
            # bcryptハッシュとの照合
            if bcrypt.checkpw(code.encode(), stored.encode()):
                codes.pop(i)
                return True, json.dumps(codes)
        elif _is_sha256_hash(stored):
            # 旧SHA-256ハッシュとの照合
            if _hash_backup_code_sha256(code) == stored:
                codes.pop(i)
                return True, json.dumps(codes)
        else:
            # 平文との照合（最旧データ互換）
            if code == stored:
                codes.pop(i)
                return True, json.dumps(codes)

    return False, stored_codes_json

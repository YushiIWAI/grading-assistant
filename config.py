"""起動時の設定検証。

FastAPI / Streamlit / CLI / Alembic から呼び出す。
APP_ENV が development / test 以外の場合、秘密情報が未設定なら起動を拒否する。
"""

from __future__ import annotations

import os
import warnings
import base64

APP_ENV = os.environ.get("APP_ENV", "development")

# --- 秘密情報の取得 ---

def _get_jwt_secret() -> str:
    val = os.environ.get("JWT_SECRET_KEY", "")
    if not val and APP_ENV in ("development", "test"):
        return "dev-secret-do-not-use-in-production"
    return val


def _get_audit_hmac_key() -> bytes:
    """AUDIT_HMAC_KEY を JWT_SECRET_KEY とは独立に取得する。"""
    key = os.environ.get("AUDIT_HMAC_KEY", "")
    if not key and APP_ENV in ("development", "test"):
        return b"dev-audit-key"
    return key.encode()


def _get_encryption_key() -> str:
    return os.environ.get("ENCRYPTION_KEY", "")


# --- 検証 ---


class ConfigurationError(RuntimeError):
    """秘密情報の設定不備による起動拒否。"""


def validate_secrets() -> None:
    """本番環境で秘密情報が未設定の場合に起動を拒否する。

    APP_ENV が development / test の場合は警告のみ。
    """
    is_dev = APP_ENV in ("development", "test")

    jwt_secret = os.environ.get("JWT_SECRET_KEY", "")
    audit_hmac_key = os.environ.get("AUDIT_HMAC_KEY", "")
    encryption_key = _get_encryption_key()

    errors: list[str] = []

    if not jwt_secret:
        msg = "JWT_SECRET_KEY が未設定です。本番ではJWT偽造を許します。"
        if is_dev:
            warnings.warn(msg, stacklevel=2)
        else:
            errors.append(msg)

    if not audit_hmac_key:
        msg = "AUDIT_HMAC_KEY が未設定です。監査ログの改ざん検知が機能しません。"
        if is_dev:
            warnings.warn(msg, stacklevel=2)
        else:
            errors.append(msg)

    if not encryption_key:
        msg = "ENCRYPTION_KEY が未設定です。機密データが平文で保存されます。"
        if is_dev:
            warnings.warn(msg, stacklevel=2)
        else:
            errors.append(msg)
    elif encryption_key:
        # Fernet鍵の形式検証（32バイトをbase64urlエンコードした44文字）
        try:
            decoded = base64.urlsafe_b64decode(encryption_key)
            if len(decoded) != 32:
                raise ValueError("Fernet key must be 32 url-safe base64-encoded bytes")
        except Exception:
            msg = "ENCRYPTION_KEY の形式が不正です。Fernet鍵（32バイトのbase64url）を設定してください。"
            if is_dev:
                warnings.warn(msg, stacklevel=2)
            else:
                errors.append(msg)

    if errors:
        raise ConfigurationError(
            "本番環境で必須の秘密情報が設定されていません:\n  - " + "\n  - ".join(errors)
        )

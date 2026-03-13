"""保存時暗号化（at rest）— Fernet対称暗号を使用。

環境変数 ENCRYPTION_KEY が設定されていれば暗号化を有効にする。
未設定の場合は暗号化をスキップし、平文のまま保存する（開発モード）。

ENCRYPTION_KEY の生成:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import base64
import json
import os

_ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

_fernet = None


def _get_fernet():
    """Fernetインスタンスを遅延初期化する。"""
    global _fernet
    if _fernet is None and _ENCRYPTION_KEY:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_ENCRYPTION_KEY.encode())
    return _fernet


def is_encryption_enabled() -> bool:
    """暗号化が有効かどうかを返す。"""
    return bool(_ENCRYPTION_KEY)


def encrypt_json(data) -> str | None:
    """JSON化可能なデータを暗号化して Base64 文字列にする。

    暗号化無効時はNoneを返す（呼び出し側で平文保存にフォールバック）。
    """
    f = _get_fernet()
    if f is None:
        return None
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(f.encrypt(plaintext)).decode("ascii")


def decrypt_json(encrypted: str):
    """暗号化された Base64 文字列をデコード・復号してPythonオブジェクトに戻す。

    暗号化無効時やデコード失敗時はNoneを返す。
    """
    f = _get_fernet()
    if f is None:
        return None
    try:
        ciphertext = base64.urlsafe_b64decode(encrypted.encode("ascii"))
        plaintext = f.decrypt(ciphertext)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def encrypt_text(text: str) -> str | None:
    """テキストを暗号化する。暗号化無効時はNoneを返す。"""
    f = _get_fernet()
    if f is None:
        return None
    return base64.urlsafe_b64encode(f.encrypt(text.encode("utf-8"))).decode("ascii")


def decrypt_text(encrypted: str) -> str | None:
    """暗号化されたテキストを復号する。"""
    f = _get_fernet()
    if f is None:
        return None
    try:
        ciphertext = base64.urlsafe_b64decode(encrypted.encode("ascii"))
        return f.decrypt(ciphertext).decode("utf-8")
    except Exception:
        return None

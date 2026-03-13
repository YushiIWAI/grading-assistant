"""暗号化モジュールのテスト"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import encryption


class TestEncryption:
    """encryption.py のテスト"""

    def test_disabled_by_default(self):
        """ENCRYPTION_KEY未設定時は暗号化無効"""
        with patch.object(encryption, "_ENCRYPTION_KEY", ""):
            encryption._fernet = None
            assert not encryption.is_encryption_enabled()
            assert encryption.encrypt_json({"test": 1}) is None
            assert encryption.decrypt_json("dummy") is None
            assert encryption.encrypt_text("hello") is None
            assert encryption.decrypt_text("dummy") is None

    def test_encrypt_decrypt_json(self):
        """JSON暗号化・復号の往復"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            assert encryption.is_encryption_enabled()

            data = {"students": [{"id": "S001", "name": "山田太郎"}], "scores": [1, 2, 3]}
            encrypted = encryption.encrypt_json(data)
            assert encrypted is not None
            assert encrypted != str(data)

            decrypted = encryption.decrypt_json(encrypted)
            assert decrypted == data

    def test_encrypt_decrypt_text(self):
        """テキスト暗号化・復号の往復"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            original = "氏名: 山田太郎, 得点: 85"
            encrypted = encryption.encrypt_text(original)
            assert encrypted is not None
            assert encrypted != original

            decrypted = encryption.decrypt_text(encrypted)
            assert decrypted == original

    def test_decrypt_with_wrong_key_returns_none(self):
        """異なる鍵での復号はNoneを返す"""
        from cryptography.fernet import Fernet
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        with patch.object(encryption, "_ENCRYPTION_KEY", key1):
            encryption._fernet = None
            encrypted = encryption.encrypt_json({"secret": True})

        with patch.object(encryption, "_ENCRYPTION_KEY", key2):
            encryption._fernet = None
            result = encryption.decrypt_json(encrypted)
            assert result is None

    def test_encrypt_empty_data(self):
        """空データの暗号化"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            encrypted = encryption.encrypt_json([])
            decrypted = encryption.decrypt_json(encrypted)
            assert decrypted == []

    def test_encrypt_unicode(self):
        """Unicode（日本語）の暗号化"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        with patch.object(encryption, "_ENCRYPTION_KEY", key):
            encryption._fernet = None
            data = {"回答": "吾輩は猫である。名前はまだ無い。"}
            encrypted = encryption.encrypt_json(data)
            decrypted = encryption.decrypt_json(encrypted)
            assert decrypted == data

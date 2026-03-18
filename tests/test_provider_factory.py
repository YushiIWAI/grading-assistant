"""provider_factory.py のユニットテスト"""

import pytest

from pdf_processor import PrivacyMaskConfig
from provider_factory import build_provider
from scoring_engine import DemoProvider, GeminiProvider, AnthropicProvider


def test_build_provider_demo_explicit():
    """demo 明示指定時は DemoProvider を返す"""
    config = PrivacyMaskConfig(enabled=False, strategy="top_left")
    provider, resolved = build_provider(
        provider_name="demo",
        api_key="",
        privacy_mask=config,
    )
    assert isinstance(provider, DemoProvider)
    assert resolved == "demo"
    assert provider.privacy_mask == config


def test_build_provider_gemini_without_key_raises():
    """gemini 指定で APIキー未設定時は ValueError"""
    with pytest.raises(ValueError, match="APIキーが設定されていません"):
        build_provider(provider_name="gemini", api_key="")


def test_build_provider_anthropic_without_key_raises():
    """anthropic 指定で APIキー未設定時は ValueError"""
    with pytest.raises(ValueError, match="APIキーが設定されていません"):
        build_provider(provider_name="anthropic", api_key="")


def test_build_provider_gemini_with_key():
    """gemini 指定 + APIキーあり → GeminiProvider"""
    provider, resolved = build_provider(
        provider_name="gemini", api_key="test-key-123"
    )
    assert isinstance(provider, GeminiProvider)
    assert resolved == "gemini"


def test_build_provider_anthropic_with_key():
    """anthropic 指定 + APIキーあり → AnthropicProvider"""
    provider, resolved = build_provider(
        provider_name="anthropic", api_key="test-key-456"
    )
    assert isinstance(provider, AnthropicProvider)
    assert resolved == "anthropic"


def test_build_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="未対応の provider"):
        build_provider("unsupported")

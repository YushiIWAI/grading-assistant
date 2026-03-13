"""provider_factory.py のユニットテスト"""

import pytest

from pdf_processor import PrivacyMaskConfig
from provider_factory import build_provider
from scoring_engine import DemoProvider


@pytest.mark.parametrize("provider_name", ["demo", "gemini", "anthropic"])
def test_build_provider_uses_demo_with_missing_or_demo_config(provider_name):
    config = PrivacyMaskConfig(enabled=False, strategy="top_left")

    provider = build_provider(
        provider_name=provider_name,
        api_key="",
        privacy_mask=config,
    )

    assert isinstance(provider, DemoProvider)
    assert provider.privacy_mask == config


def test_build_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="未対応の provider"):
        build_provider("unsupported")

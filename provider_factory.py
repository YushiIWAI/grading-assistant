"""採点プロバイダーの構築ユーティリティ。"""

from __future__ import annotations

from pdf_processor import PrivacyMaskConfig
from scoring_engine import AnthropicProvider, DemoProvider, GeminiProvider


def build_provider(
    provider_name: str,
    api_key: str = "",
    model_name: str = "",
    privacy_mask: PrivacyMaskConfig | None = None,
):
    """設定値から採点プロバイダーを生成する。"""
    if provider_name == "gemini":
        if not api_key:
            return DemoProvider(privacy_mask=privacy_mask)
        return GeminiProvider(
            api_key,
            model_name or "gemini-3.1-pro-preview",
            privacy_mask=privacy_mask,
        )
    if provider_name == "anthropic":
        if not api_key:
            return DemoProvider(privacy_mask=privacy_mask)
        return AnthropicProvider(
            api_key,
            model_name or "claude-sonnet-4-20250514",
            privacy_mask=privacy_mask,
        )
    if provider_name == "demo":
        return DemoProvider(privacy_mask=privacy_mask)
    raise ValueError(f"未対応の provider です: {provider_name}")

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
    """設定値から採点プロバイダーを生成する。

    Returns:
        (provider_instance, resolved_provider_name) のタプル。
        resolved_provider_name は監査ログに記録すべき実際のプロバイダー名。

    Raises:
        ValueError: gemini/anthropic 指定時に api_key が未設定の場合。
    """
    if provider_name == "gemini":
        if not api_key:
            raise ValueError(
                "Gemini APIキーが設定されていません。"
                "管理者画面でAPIキーを設定するか、provider=demo を使用してください。"
            )
        return GeminiProvider(
            api_key,
            model_name or "gemini-3.1-pro-preview",
            privacy_mask=privacy_mask,
        ), "gemini"
    if provider_name == "anthropic":
        if not api_key:
            raise ValueError(
                "Anthropic APIキーが設定されていません。"
                "管理者画面でAPIキーを設定するか、provider=demo を使用してください。"
            )
        return AnthropicProvider(
            api_key,
            model_name or "claude-sonnet-4-20250514",
            privacy_mask=privacy_mask,
        ), "anthropic"
    if provider_name == "demo":
        return DemoProvider(privacy_mask=privacy_mask), "demo"
    raise ValueError(f"未対応の provider です: {provider_name}")

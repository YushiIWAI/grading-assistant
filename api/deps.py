"""FastAPI 認証依存関係"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth import decode_token

_security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """JWTから抽出した認証済みユーザー情報"""
    user_id: str
    school_id: str
    role: str


def _check_token_invalidation(payload: dict) -> None:
    """トークンが失効済みでないか確認する。

    ユーザーの token_invalidated_at より前に発行されたトークンを拒否する。
    パスワード変更・MFA変更時に全トークンを一括失効させるための仕組み。
    """
    from storage import get_user
    user = get_user(payload["sub"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザーが見つかりません",
        )
    if user.token_invalidated_at:
        token_iat = payload.get("iat", 0)
        # token_invalidated_at は ISO 形式（サブ秒精度）、iat は整数秒の Unix timestamp。
        # サブ秒の差で誤判定しないよう、invalidated_ts を秒単位に切り捨てて比較する。
        invalidated_ts = int(datetime.fromisoformat(user.token_invalidated_at).timestamp())
        if token_iat < invalidated_ts:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="トークンが失効しています。再ログインしてください。",
            )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser:
    """認証必須の依存関係。JWT からユーザー情報を抽出する。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です",
        )
    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="トークンが無効です",
        )
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="アクセストークンが必要です",
        )
    _check_token_invalidation(payload)
    return CurrentUser(
        user_id=payload["sub"],
        school_id=payload["school_id"],
        role=payload["role"],
    )


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser | None:
    """認証オプショナルの依存関係。ヘッダーなし → None（後方互換）。"""
    if credentials is None:
        return None
    return get_current_user(credentials)

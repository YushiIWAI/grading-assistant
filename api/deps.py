"""FastAPI 認証依存関係"""

from __future__ import annotations

from dataclasses import dataclass

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

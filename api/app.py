"""grading-assistant の API レイヤー。"""

from __future__ import annotations

import base64
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.deps import CurrentUser, get_current_user, get_optional_user
from auth import (
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    decode_token,
    generate_mfa_secret,
    get_totp_uri,
    hash_password,
    verify_backup_code,
    verify_password,
    verify_totp,
)
from models import ScoringSession
from pdf_processor import PrivacyMaskConfig, pdf_to_images, split_pages_by_student
from provider_factory import build_provider
from rubric_io import (
    load_rubric_from_yaml,
    rubric_from_dict,
    rubric_summary,
    rubric_to_yaml,
)
from scoring_engine import ocr_all_students, run_horizontal_grading
from starlette.responses import JSONResponse
from storage import (
    DecryptionError,
    change_password,
    delete_api_key,
    delete_school_data,
    delete_session,
    disable_mfa,
    enable_mfa,
    export_csv,
    export_school_data,
    get_api_key,
    get_school,
    get_user,
    get_user_by_email,
    list_api_keys,
    list_audit_logs,
    list_sessions,
    load_session,
    log_audit_event,
    purge_expired_sessions,
    save_api_key,
    save_session,
    setup_mfa,
    store_refresh_token,
    update_mfa_backup_codes,
    use_refresh_token,
    verify_audit_chain,
)


# --- Request / Response Models ---


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class MfaEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class MfaDisableRequest(BaseModel):
    password: str = Field(..., min_length=1)


class ApiKeySetRequest(BaseModel):
    provider: str = Field(..., pattern="^(gemini|anthropic)$")
    api_key: str = Field(..., min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class RubricParseRequest(BaseModel):
    yaml_text: str = Field(..., min_length=1)


class RubricRenderRequest(BaseModel):
    rubric: dict[str, Any]


class SessionCreateRequest(BaseModel):
    rubric_title: str = Field(..., min_length=1)
    pdf_filename: str = "uploaded.pdf"
    pages_per_student: int = Field(default=1, ge=1)


class PrivacyMaskRequest(BaseModel):
    enabled: bool = True
    strategy: str = "top_right"
    width_ratio: float = 0.36
    height_ratio: float = 0.14
    margin_x_ratio: float = 0.03
    margin_y_ratio: float = 0.02
    first_page_only: bool = True


class ProviderConfigRequest(BaseModel):
    provider: str = "demo"
    api_key: str = ""
    model_name: str = ""
    privacy_mask: PrivacyMaskRequest | None = None


class RubricRefineRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    provider: ProviderConfigRequest


class OcrRunRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    pdf_base64: str = Field(..., min_length=1)
    provider: ProviderConfigRequest
    enable_two_stage: bool = True
    submission_type: str = "handwritten"  # "typed" | "handwritten"


class HorizontalGradingRunRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    provider: ProviderConfigRequest
    batch_size: int = Field(default=15, ge=1, le=100)
    enable_verification: bool = False
    student_ids_to_grade: list[str] | None = None
    is_typed: bool = False


from config import validate_secrets

# 起動時に秘密情報の設定を検証（本番で未設定なら起動拒否）
validate_secrets()

app = FastAPI(
    title="grading-assistant API",
    version="0.3.0",
    description=(
        "採点支援APIレイヤー。"
        "ルーブリック変換、セッション永続化、OCR/採点実行、認証を担当する。"
    ),
)


@app.exception_handler(DecryptionError)
async def _decryption_error_handler(request: Request, exc: DecryptionError):
    """暗号化キー不一致時に 503 を返す。"""
    return JSONResponse(
        status_code=503,
        content={
            "detail": "暗号化キーの設定エラーにより、データの復号に失敗しました。管理者に連絡してください。",
        },
    )


# --- Rate Limiting ---

# 信頼済みプロキシのIPリスト（TRUSTED_PROXIES 環境変数でカンマ区切り指定）
# この設定がない場合、X-Forwarded-For ヘッダーは無視してクライアント直接IPを使用する。
import os as _os
_TRUSTED_PROXIES: set[str] = set(
    p.strip() for p in _os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()
)


def _get_redis():
    """Redis クライアントを取得する。REDIS_URL 未設定時は None。"""
    redis_url = _os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        return redis.from_url(redis_url, decode_responses=True)
    except Exception:
        return None


_redis_client = _get_redis()


class _InMemoryRateLimiter:
    """インメモリ・スライディングウィンドウ方式のレート制限。

    シングルワーカー環境向け。マルチワーカーでは Redis 版を使用すること。
    """

    def __init__(self, max_requests: int, window: int):
        self.max_requests = max_requests
        self.window = window
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """制限内ならTrue、超過ならFalse。"""
        now = time.time()
        cutoff = now - self.window
        hits = self._hits[key]
        self._hits[key] = [t for t in hits if t > cutoff]
        if len(self._hits[key]) >= self.max_requests:
            return False
        self._hits[key].append(now)
        return True

    def reset(self) -> None:
        """全エントリをクリアする（テスト用）。"""
        self._hits.clear()


class _RedisRateLimiter:
    """Redis ベースのスライディングウィンドウ方式レート制限。

    マルチワーカー環境で共有カウンタとして機能する。
    Sorted Set を使用し、window 秒以内のタイムスタンプを管理する。
    """

    def __init__(self, max_requests: int, window: int, prefix: str = "rl"):
        self.max_requests = max_requests
        self.window = window
        self.prefix = prefix

    def check(self, key: str) -> bool:
        """制限内ならTrue、超過ならFalse。"""
        if _redis_client is None:
            return True  # Redis 不通時はフェイルオープン（別途インメモリで保護）
        redis_key = f"{self.prefix}:{key}"
        now = time.time()
        cutoff = now - self.window
        pipe = _redis_client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, cutoff)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {str(now): now})
        pipe.expire(redis_key, self.window + 1)
        results = pipe.execute()
        current_count = results[1]
        return current_count < self.max_requests

    def reset(self) -> None:
        """全エントリをクリアする（テスト用）。"""
        if _redis_client is not None:
            for key in _redis_client.scan_iter(f"{self.prefix}:*"):
                _redis_client.delete(key)


def _create_limiter(max_requests: int, window: int, prefix: str = "rl"):
    """環境に応じたレートリミッターを生成する。Redis があれば Redis 版を使用。"""
    if _redis_client is not None:
        return _RedisRateLimiter(max_requests, window, prefix)
    return _InMemoryRateLimiter(max_requests, window)


# login: 10回/分、mfa/verify: 5回/分
_login_limiter = _create_limiter(max_requests=10, window=60, prefix="rl:login")
_mfa_verify_limiter = _create_limiter(max_requests=5, window=60, prefix="rl:mfa")


def _get_client_ip(request: Request) -> str:
    """リクエストからクライアントIPを取得する。

    X-Forwarded-For は TRUSTED_PROXIES に含まれるプロキシからのリクエストのみ信頼する。
    信頼済みプロキシ未設定の場合、ヘッダーは無視してクライアント直接IPを使用する。
    """
    direct_ip = request.client.host if request.client else "unknown"
    if not _TRUSTED_PROXIES:
        return direct_ip
    if direct_ip not in _TRUSTED_PROXIES:
        return direct_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return direct_ip


def _check_rate_limit(request: Request, limiter) -> None:
    """レート制限を確認し、超過時は429を返す。"""
    client_ip = _get_client_ip(request)
    if not limiter.check(client_ip):
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらく待ってから再試行してください。",
        )


# --- Helpers ---


def _build_provider_from_request(
    config: ProviderConfigRequest,
    school_id: str | None = None,
) -> tuple:
    """プロバイダーを構築する。

    Returns:
        (provider_instance, resolved_provider_name) タプル
    """
    privacy_mask = PrivacyMaskConfig(
        **(config.privacy_mask.model_dump() if config.privacy_mask else {})
    )
    # リクエストにAPIキーがなければ、DBから学校のキーを取得
    resolved_api_key = config.api_key
    if not resolved_api_key and school_id and config.provider in ("gemini", "anthropic"):
        stored_key = get_api_key(school_id, config.provider)
        if stored_key:
            resolved_api_key = stored_key
    try:
        return build_provider(
            provider_name=config.provider,
            api_key=resolved_api_key,
            model_name=config.model_name,
            privacy_mask=privacy_mask,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_session_for_user(
    session_id: str, user: CurrentUser
) -> ScoringSession:
    """セッションを読み込む。テナント検証付き。"""
    session = load_session(session_id, school_id=user.school_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _require_admin(user: CurrentUser) -> None:
    """管理者権限を要求する。"""
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="管理者権限が必要です")


def _require_admin_for_school(user: CurrentUser, school_id: str) -> None:
    """管理者権限を要求し、対象学校へのアクセス権を検証する。

    superadmin は全校アクセス可能、admin は自校のみ。
    """
    _require_admin(user)
    if user.role != "superadmin" and user.school_id != school_id:
        raise HTTPException(status_code=403, detail="他校のデータにはアクセスできません")


def _store_refresh(jti: str, user_id: str, family_id: str) -> None:
    """リフレッシュトークンをDBに記録する。"""
    from auth import REFRESH_TOKEN_EXPIRE_DAYS
    expires_at = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    store_refresh_token(jti, user_id, family_id, expires_at)


# --- Auth Endpoints ---


@app.post("/api/v1/auth/login")
def login(request: LoginRequest, http_request: Request) -> dict[str, Any]:
    """メール+パスワードでログインし、JWTトークンを返す。
    MFA有効時は mfa_required=True と mfa_token を返す。
    レート制限: 10回/分（IPアドレス単位）
    """
    _check_rate_limit(http_request, _login_limiter)
    user = get_user_by_email(request.email)
    if user is None or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="アカウントが無効です")

    # MFA有効時: パスワード認証のみ完了。TOTP検証待ちの一時トークンを返す
    if user.mfa_enabled:
        mfa_token = create_mfa_pending_token(user.id)
        log_audit_event(
            action="login_mfa_pending",
            resource_type="user",
            resource_id=user.id,
            user_id=user.id,
            school_id=user.school_id,
            details={"email": user.email},
        )
        return {
            "mfa_required": True,
            "mfa_token": mfa_token,
        }

    # MFA無効時: 通常どおりトークン発行
    school = get_school(user.school_id)
    school_name = school.name if school else ""

    access_token = create_access_token(user.id, user.school_id, user.role)
    refresh_token, jti, family_id = create_refresh_token(user.id)
    _store_refresh(jti, user.id, family_id)

    log_audit_event(
        action="login",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
        details={"email": user.email},
    )

    return {
        "mfa_required": False,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "school_id": user.school_id,
            "school_name": school_name,
        },
    }


@app.post("/api/v1/auth/refresh")
def refresh(request: RefreshRequest) -> dict[str, Any]:
    """リフレッシュトークンから新しいアクセストークン+リフレッシュトークンを発行する。

    トークンローテーション: 使用済みのリフレッシュトークンは即座に無効化される。
    再利用が検知された場合（盗難疑い）、ファミリー全体が無効化される。
    """
    try:
        payload = decode_token(request.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="リフレッシュトークンが無効です")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="リフレッシュトークンが必要です")

    user = get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    # token_invalidated_at より前に発行されたリフレッシュトークンを拒否
    if user.token_invalidated_at:
        token_iat = payload.get("iat", 0)
        invalidated_ts = int(datetime.fromisoformat(user.token_invalidated_at).timestamp())
        if token_iat < invalidated_ts:
            raise HTTPException(status_code=401, detail="トークンが失効しています。再ログインしてください。")

    # jti によるトークンファミリー検証（DB にトークンがある場合）
    jti = payload.get("jti")
    family_id = payload.get("family_id")
    if jti:
        token_record = use_refresh_token(jti)
        if token_record is None:
            # revoke 済み or 不存在 → 盗難疑い
            raise HTTPException(
                status_code=401,
                detail="リフレッシュトークンが無効化されています。再ログインしてください。",
            )

    # 新しいトークンペアを発行（ローテーション）
    access_token = create_access_token(user.id, user.school_id, user.role)
    new_refresh, new_jti, new_family = create_refresh_token(user.id, family_id=family_id)
    _store_refresh(new_jti, user.id, new_family)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@app.get("/api/v1/auth/me")
def me(
    current_user: CurrentUser | None = Depends(get_optional_user),
) -> dict[str, Any]:
    """認証済みユーザーの情報を返す。未認証時は空レスポンス。"""
    if current_user is None:
        return {"authenticated": False}

    user = get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    school = get_school(user.school_id)
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "school_id": user.school_id,
            "school_name": school.name if school else "",
            "mfa_enabled": user.mfa_enabled,
        },
    }


@app.post("/api/v1/auth/change-password")
def change_password_endpoint(
    request: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """パスワードを変更する。現在のパスワードで再認証後、新パスワードを設定。

    変更後は全トークンが無効化されるため、再ログインが必要。
    """
    user = get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    if not verify_password(request.current_password, user.hashed_password):
        raise HTTPException(status_code=401, detail="現在のパスワードが正しくありません")

    new_hashed = hash_password(request.new_password)
    change_password(user.id, new_hashed)

    log_audit_event(
        action="change_password",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
    )

    return {"message": "パスワードを変更しました。再ログインしてください。"}


# --- MFA Endpoints ---


def _resolve_mfa_pending_user(mfa_token: str):
    """mfa_pending トークンからユーザーを取得する。検証失敗時はHTTPExceptionを送出。"""
    try:
        payload = decode_token(mfa_token)
    except Exception:
        raise HTTPException(status_code=401, detail="MFAトークンが無効または期限切れです")
    if payload.get("type") != "mfa_pending":
        raise HTTPException(status_code=401, detail="MFA検証待ちトークンが必要です")
    user = get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")
    return user


@app.post("/api/v1/auth/mfa/verify")
def mfa_verify(request: MfaVerifyRequest, http_request: Request) -> dict[str, Any]:
    """MFA検証（ログイン第2段階）。TOTPコードまたはバックアップコードで認証を完了する。
    レート制限: 5回/分（IPアドレス単位）
    """
    _check_rate_limit(http_request, _mfa_verify_limiter)
    user = _resolve_mfa_pending_user(request.mfa_token)

    if not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(status_code=400, detail="MFAが設定されていません")

    # TOTPコード検証を試行
    verified = verify_totp(user.mfa_secret, request.code)

    # TOTP失敗 → バックアップコード検証を試行
    if not verified and user.mfa_backup_codes:
        backup_valid, updated_codes = verify_backup_code(user.mfa_backup_codes, request.code)
        if backup_valid:
            verified = True
            update_mfa_backup_codes(user.id, updated_codes)

    if not verified:
        log_audit_event(
            action="mfa_verify_failed",
            resource_type="user",
            resource_id=user.id,
            user_id=user.id,
            school_id=user.school_id,
        )
        raise HTTPException(status_code=401, detail="認証コードが正しくありません")

    # MFA検証成功 → 本トークン発行
    school = get_school(user.school_id)
    school_name = school.name if school else ""

    access_token = create_access_token(user.id, user.school_id, user.role)
    refresh_token, jti, family_id = create_refresh_token(user.id)
    _store_refresh(jti, user.id, family_id)

    log_audit_event(
        action="login",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
        details={"email": user.email, "mfa": True},
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "school_id": user.school_id,
            "school_name": school_name,
            "mfa_enabled": True,
        },
    }


@app.post("/api/v1/auth/mfa/setup")
def mfa_setup(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """MFAセットアップ: シークレット生成 + QRコード用URI返却。認証必須。"""
    user = get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFAは既に有効です。無効化してから再設定してください")

    secret = generate_mfa_secret()
    setup_mfa(user.id, secret)

    totp_uri = get_totp_uri(secret, user.email)

    log_audit_event(
        action="mfa_setup",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
    )

    return {
        "secret": secret,
        "totp_uri": totp_uri,
    }


@app.post("/api/v1/auth/mfa/enable")
def mfa_enable(
    request: MfaEnableRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """MFA有効化: 最初のTOTPコードを検証してMFAを有効化する。バックアップコードを返す。"""
    user = get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFAは既に有効です")

    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail="先に /api/v1/auth/mfa/setup を実行してください")

    if not verify_totp(user.mfa_secret, request.code):
        raise HTTPException(status_code=400, detail="認証コードが正しくありません。認証アプリのコードを確認してください")

    backup_codes = enable_mfa(user.id)

    log_audit_event(
        action="mfa_enabled",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
    )

    return {
        "mfa_enabled": True,
        "backup_codes": backup_codes,
    }


@app.post("/api/v1/auth/mfa/disable")
def mfa_disable(
    request: MfaDisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """MFA無効化: パスワード再確認の上、MFAを無効化する。"""
    user = get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")

    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFAは無効です")

    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="パスワードが正しくありません")

    disable_mfa(user.id)

    log_audit_event(
        action="mfa_disabled",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        school_id=user.school_id,
    )

    return {"mfa_enabled": False}


# --- Audit Log Endpoints ---


@app.get("/api/v1/audit-logs")
def get_audit_logs(
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """監査ログを取得する。管理者以上のみ。テナントスコープ。"""
    _require_admin(current_user)
    logs = list_audit_logs(
        school_id=current_user.school_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
        offset=offset,
    )
    return {"audit_logs": logs}


@app.get("/api/v1/audit-logs/verify")
def verify_audit_logs(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """監査ログチェーン全体の整合性を検証する（superadmin専用）。

    チェーンはグローバル（全校混合）なので、全ログを対象に検証する。
    """
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="superadmin権限が必要です")
    is_valid, errors = verify_audit_chain()
    return {"is_valid": is_valid, "errors": errors}


# --- Health Check ---


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "grading-assistant-api",
        "storage_mode": "local-json-bridge",
    }


# --- Rubric Endpoints ---


@app.post("/api/v1/rubrics/parse")
def parse_rubric(request: RubricParseRequest) -> dict[str, Any]:
    try:
        rubric = load_rubric_from_yaml(request.yaml_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "rubric": asdict(rubric),
        "summary": rubric_summary(rubric),
    }


@app.post("/api/v1/rubrics/render", response_class=PlainTextResponse)
def render_rubric(request: RubricRenderRequest) -> str:
    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return rubric_to_yaml(rubric)


@app.post("/api/v1/rubrics/refine")
def refine_rubric(
    request: RubricRefineRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """OCR結果を基に、採点基準の精緻化質問を生成する。"""
    session = _load_session_for_user(request.session_id, current_user)

    if not session.ocr_results:
        raise HTTPException(status_code=400, detail="OCR結果がありません。先にOCRを実行してください。")

    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # OCR結果を question_id → [(student_id, text)] に変換
    ocr_answers_by_question: dict[str, list[tuple[str, str]]] = {}
    for ocr in session.ocr_results:
        if ocr.status not in ("ocr_done", "reviewed"):
            continue
        for ans in ocr.answers:
            ocr_answers_by_question.setdefault(ans.question_id, []).append(
                (ocr.student_id, ans.transcribed_text)
            )

    provider, _ = _build_provider_from_request(request.provider, school_id=current_user.school_id)
    result = provider.refine_rubric(rubric, ocr_answers_by_question)

    return {"questions": result.get("questions", [])}


# --- Session Endpoints ---


@app.get("/api/v1/sessions")
def get_sessions(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    return {"sessions": list_sessions(school_id=current_user.school_id)}


@app.post("/api/v1/sessions", status_code=201)
def create_session(
    request: SessionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session = ScoringSession(
        rubric_title=request.rubric_title,
        pdf_filename=request.pdf_filename,
        pages_per_student=request.pages_per_student,
    )
    save_session(
        session,
        school_id=current_user.school_id,
        created_by=current_user.user_id,
    )
    log_audit_event(
        action="create",
        resource_type="session",
        resource_id=session.session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
        details={"rubric_title": request.rubric_title},
    )
    return {"session": session.to_dict()}


@app.get("/api/v1/sessions/{session_id}")
def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session = _load_session_for_user(session_id, current_user)
    return {"session": session.to_dict()}


@app.put("/api/v1/sessions/{session_id}")
def put_session(
    session_id: str,
    payload: dict[str, Any],
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    # テナント検証: 既存セッションがあればアクセス権を確認
    existing = load_session(session_id, school_id=current_user.school_id)
    # 他テナントの既存セッションへの上書きを防ぐ
    if existing is None:
        any_session = load_session(session_id)
        if any_session is not None:
            raise HTTPException(status_code=404, detail="session not found")

    body_session_id = payload.get("session_id")
    if body_session_id and body_session_id != session_id:
        raise HTTPException(
            status_code=400,
            detail="path の session_id と payload の session_id が一致しません",
        )

    payload["session_id"] = session_id
    try:
        session = ScoringSession.from_dict(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_session(
        session,
        school_id=current_user.school_id,
        created_by=current_user.user_id,
    )
    log_audit_event(
        action="update",
        resource_type="session",
        resource_id=session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
    )
    return {"session": session.to_dict()}


@app.get("/api/v1/sessions/{session_id}/exports/csv", response_class=PlainTextResponse)
def export_session_csv(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> str:
    session = _load_session_for_user(session_id, current_user)
    log_audit_event(
        action="export",
        resource_type="session",
        resource_id=session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
        details={"format": "csv"},
    )
    return export_csv(session)


@app.delete("/api/v1/sessions/{session_id}")
def remove_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """セッションを削除する。"""
    deleted = delete_session(
        session_id,
        school_id=current_user.school_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": True, "session_id": session_id}


# --- Data Management Endpoints ---


@app.post("/api/v1/admin/purge-expired")
def admin_purge_expired(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """保存期間を超えたセッションを一括削除する（管理者用）。

    テナント管理者は自校のみ、superadmin は全校対象。
    """
    _require_admin(current_user)
    if current_user.role == "superadmin":
        purged = purge_expired_sessions(user_id=current_user.user_id)
    else:
        purged = purge_expired_sessions(school_id=current_user.school_id, user_id=current_user.user_id)
    return {"purged_count": len(purged), "session_ids": purged}


@app.get("/api/v1/admin/schools/{school_id}/export")
def admin_export_school(
    school_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """学校の全データをエクスポートする（解約・データポータビリティ用）。"""
    _require_admin_for_school(current_user, school_id)
    data = export_school_data(school_id, user_id=current_user.user_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@app.delete("/api/v1/admin/schools/{school_id}")
def admin_delete_school(
    school_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """学校の全データを削除する（解約時）。

    注意: 監査ログは規制遵守のため匿名化して保持される。
    """
    _require_admin_for_school(current_user, school_id)
    summary = delete_school_data(
        school_id,
        user_id=current_user.user_id,
    )
    return summary


# --- API Key Management Endpoints ---


@app.post("/api/v1/admin/api-keys")
def set_api_key_endpoint(
    request: ApiKeySetRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """APIキーを暗号化保存する（管理者専用）。"""
    _require_admin(current_user)

    result = save_api_key(
        school_id=current_user.school_id,
        provider=request.provider,
        api_key=request.api_key,
        created_by=current_user.user_id,
    )
    return result


@app.get("/api/v1/admin/api-keys")
def get_api_keys_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """設定済みAPIキーの一覧を返す（キー本体は含まない。管理者専用）。"""
    _require_admin(current_user)

    keys = list_api_keys(current_user.school_id)
    return {"api_keys": keys}


@app.delete("/api/v1/admin/api-keys/{provider}")
def delete_api_key_endpoint(
    provider: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """APIキーを削除する（管理者専用）。"""
    _require_admin(current_user)

    deleted = delete_api_key(
        school_id=current_user.school_id,
        provider=provider,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="APIキーが見つかりません")
    return {"deleted": True, "provider": provider}


# --- Run Endpoints ---


@app.post("/api/v1/runs/ocr")
def run_ocr(
    request: OcrRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session = _load_session_for_user(request.session_id, current_user)

    try:
        rubric = rubric_from_dict(request.rubric)
        pdf_bytes = base64.b64decode(request.pdf_base64)
        images = pdf_to_images(pdf_bytes, submission_type=request.submission_type)
        student_groups = split_pages_by_student(images, rubric.pages_per_student)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider, resolved_provider = _build_provider_from_request(request.provider, school_id=current_user.school_id)

    ocr_results, errors = ocr_all_students(
        provider=provider,
        student_groups=student_groups,
        rubric=rubric,
        enable_two_stage=request.enable_two_stage,
        submission_type=request.submission_type,
    )

    session.rubric_title = rubric.title
    session.pages_per_student = rubric.pages_per_student
    session.ocr_results = ocr_results
    save_session(session)

    log_audit_event(
        action="run_ocr",
        resource_type="session",
        resource_id=request.session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
        details={
            "requested_provider": request.provider.provider,
            "resolved_provider": resolved_provider,
            "student_count": len(student_groups),
            "error_count": len(errors),
        },
    )

    return {
        "session": session.to_dict(),
        "errors": errors,
        "student_count": len(student_groups),
    }


class ColumnMappingRequest(BaseModel):
    class_col: int | None = Field(None, ge=0)
    number_col: int | None = Field(None, ge=0)
    name_col: int | None = Field(None, ge=0)
    question_cols: dict[str, int] = Field(default_factory=dict)
    ignore_cols: list[int] = Field(default_factory=list)


class CsvImportRequest(BaseModel):
    session_id: str
    rubric: dict[str, Any]
    csv_content: str = Field(..., min_length=1, max_length=5_000_000)  # ~5MB上限
    column_mapping: ColumnMappingRequest


@app.post("/api/v1/runs/import-csv")
def import_csv_endpoint(
    request: CsvImportRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session = _load_session_for_user(request.session_id, current_user)

    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from csv_importer import (
        ColumnMapping,
        convert_to_ocr_results,
        parse_forms_csv,
    )

    try:
        csv_data = parse_forms_csv(request.csv_content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Pydantic で検証済みの ColumnMappingRequest → csv_importer の ColumnMapping に変換
    cm = request.column_mapping
    # question_cols の値（列インデックス）が非負であることを検証
    for qid, col_idx in cm.question_cols.items():
        if col_idx < 0:
            raise HTTPException(status_code=400, detail=f"question_cols[{qid}] の列インデックスが負数です: {col_idx}")
    mapping = ColumnMapping(
        class_col=cm.class_col,
        number_col=cm.number_col,
        name_col=cm.name_col,
        question_cols={str(k): v for k, v in cm.question_cols.items()},
        ignore_cols=[i for i in cm.ignore_cols if i >= 0],
    )

    ocr_results, errors, _teacher_scores = convert_to_ocr_results(csv_data, mapping, rubric)

    session.rubric_title = rubric.title
    session.pages_per_student = rubric.pages_per_student
    session.ocr_results = ocr_results
    save_session(session)

    log_audit_event(
        action="import_csv",
        resource_type="session",
        resource_id=request.session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
        details={
            "student_count": len(ocr_results),
            "error_count": len(errors),
        },
    )

    return {
        "session": session.to_dict(),
        "errors": errors,
        "student_count": len(ocr_results),
    }


@app.post("/api/v1/runs/horizontal-grading")
def run_horizontal(
    request: HorizontalGradingRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session = _load_session_for_user(request.session_id, current_user)

    try:
        rubric = rubric_from_dict(request.rubric)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider, resolved_provider = _build_provider_from_request(request.provider, school_id=current_user.school_id)
    errors = run_horizontal_grading(
        provider=provider,
        rubric=rubric,
        session=session,
        reference_students=session.get_reference_students() or None,
        batch_size=request.batch_size,
        student_ids_to_grade=request.student_ids_to_grade,
        enable_verification=request.enable_verification,
        is_typed=request.is_typed,
    )
    save_session(session)

    log_audit_event(
        action="run_grading",
        resource_type="session",
        resource_id=request.session_id,
        user_id=current_user.user_id,
        school_id=current_user.school_id,
        details={
            "requested_provider": request.provider.provider,
            "resolved_provider": resolved_provider,
            "batch_size": request.batch_size,
            "verification": request.enable_verification,
            "error_count": len(errors),
        },
    )

    return {
        "session": session.to_dict(),
        "errors": errors,
        "summary": session.summary(),
    }

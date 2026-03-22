"""ローカルストレージ: 採点結果のDB保存・読み込み・CSV出力"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

from auth import generate_backup_codes, hash_backup_codes, hash_password
from db import api_keys, audit_chain_pointer, audit_logs, get_engine, init_db, refresh_tokens, schools, scoring_sessions, users
from encryption import decrypt_json, decrypt_text, encrypt_json, encrypt_text, is_encryption_enabled
from models import School, ScoringSession, User

from config import _get_audit_hmac_key

# 監査ログのHMAC鍵（AUDIT_HMAC_KEY → JWT_SECRET_KEY フォールバック）
_AUDIT_HMAC_KEY = _get_audit_hmac_key()

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"


def ensure_dirs():
    """必要なディレクトリを作成する"""
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def _ensure_table():
    """テーブルが存在しなければ作成する"""
    init_db()


# --- Audit Log ---


def _compute_audit_hash(
    log_id: str,
    timestamp: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    prev_hash: str,
    school_id: str | None = None,
    *,
    # PII fields — 署名対象外（匿名化で書き換えてもチェーンを壊さない）
    user_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> str:
    """HMACベースの改ざん検知ハッシュを計算する（v2）。

    署名対象（不変コア）: log_id, timestamp, action, resource_type, resource_id,
                          school_id, prev_hash
    署名対象外（PII）: user_id, details, ip_address
        → 匿名化（delete_school_data）で書き換えてもチェーンは不変
    """
    message = (
        f"{log_id}|{timestamp}|{action}|{resource_type}|{resource_id or ''}"
        f"|{school_id or ''}|{prev_hash}"
    )
    return hmac.new(_AUDIT_HMAC_KEY, message.encode(), hashlib.sha256).hexdigest()


def _ensure_chain_pointer(conn) -> None:
    """audit_chain_pointer にシード行がなければ作成する。"""
    row = conn.execute(
        sa.select(audit_chain_pointer).where(audit_chain_pointer.c.id == 1)
    ).fetchone()
    if row is None:
        # 既存の audit_logs から最新ハッシュを取得してシード
        latest = conn.execute(
            sa.select(audit_logs.c.integrity_hash)
            .order_by(audit_logs.c.timestamp.desc())
            .limit(1)
        ).fetchone()
        conn.execute(
            audit_chain_pointer.insert().values(
                id=1,
                latest_hash=latest[0] if latest else "",
            )
        )


def _get_latest_audit_hash_with_lock(conn) -> str:
    """チェーンポインタから最新ハッシュを取得する。

    PostgreSQL では SELECT ... FOR UPDATE で行ロックを取得し、
    並行ワーカーの直列化を保証する。
    SQLite ではトランザクション内の排他ロックで同等の効果を得る。
    """
    _ensure_chain_pointer(conn)
    db_url = str(get_engine().url)
    if "postgresql" in db_url:
        # PostgreSQL: 行ロックで直列化
        row = conn.execute(
            sa.select(audit_chain_pointer.c.latest_hash)
            .where(audit_chain_pointer.c.id == 1)
            .with_for_update()
        ).fetchone()
    else:
        # SQLite: FOR UPDATE 非対応、トランザクション内排他で十分
        row = conn.execute(
            sa.select(audit_chain_pointer.c.latest_hash)
            .where(audit_chain_pointer.c.id == 1)
        ).fetchone()
    return row[0] if row else ""


def log_audit_event(
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    user_id: str | None = None,
    school_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> str:
    """監査ログを記録する。HMACチェーンで改ざんを検知可能にする。

    audit_chain_pointer テーブルの単一行をロック（PostgreSQL では FOR UPDATE、
    SQLite ではトランザクション排他）して prev_hash を取得し、
    INSERT 後にポインタを更新する。これにより並行ワーカーでもチェーンが分岐しない。

    Returns:
        作成したログエントリのID
    """
    _ensure_table()
    log_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    engine = get_engine()
    with engine.begin() as conn:
        prev_hash = _get_latest_audit_hash_with_lock(conn)
        integrity_hash = _compute_audit_hash(
            log_id, timestamp, action, resource_type, resource_id, prev_hash,
            school_id=school_id,
        )
        conn.execute(
            audit_logs.insert().values(
                id=log_id,
                timestamp=timestamp,
                user_id=user_id,
                school_id=school_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                ip_address=ip_address,
                integrity_hash=integrity_hash,
                prev_hash=prev_hash,
                hash_version=2,
            )
        )
        # チェーンポインタを更新
        conn.execute(
            audit_chain_pointer.update()
            .where(audit_chain_pointer.c.id == 1)
            .values(latest_hash=integrity_hash)
        )
    return log_id


def list_audit_logs(
    school_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """監査ログを検索する。"""
    _ensure_table()
    engine = get_engine()
    query = sa.select(audit_logs).order_by(audit_logs.c.timestamp.desc())
    if school_id is not None:
        query = query.where(audit_logs.c.school_id == school_id)
    if action is not None:
        query = query.where(audit_logs.c.action == action)
    if resource_type is not None:
        query = query.where(audit_logs.c.resource_type == resource_type)
    if resource_id is not None:
        query = query.where(audit_logs.c.resource_id == resource_id)
    query = query.limit(limit).offset(offset)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    results = []
    for row in rows:
        entry = dict(row)
        if isinstance(entry.get("details"), str):
            entry["details"] = json.loads(entry["details"])
        results.append(entry)
    return results


def verify_audit_chain(
    page_size: int = 1000,
) -> tuple[bool, list[str]]:
    """監査ログチェーン全体の整合性を検証する。

    チェーンはグローバル（全校混合）なので、school_id でのフィルタはしない。
    大量のログがある場合はページング（page_size件ずつ）で走査する。

    Returns:
        (is_valid, errors): 検証結果とエラーメッセージのリスト
    """
    _ensure_table()
    engine = get_engine()
    offset = 0
    rows = []
    with engine.connect() as conn:
        while True:
            query = (
                sa.select(audit_logs)
                .order_by(audit_logs.c.timestamp.asc())
                .limit(page_size)
                .offset(offset)
            )
            batch = conn.execute(query).mappings().all()
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

    errors = []
    prev_hash = ""
    for row in rows:
        expected = _compute_audit_hash(
            row["id"], row["timestamp"], row["action"],
            row["resource_type"], row["resource_id"], row["prev_hash"],
            school_id=row.get("school_id"),
        )
        if row["integrity_hash"] != expected:
            errors.append(f"ログ {row['id']} のハッシュが不一致（改ざんの可能性）")
        if row["prev_hash"] != prev_hash:
            errors.append(f"ログ {row['id']} のチェーンが断絶（prev_hash不一致）")
        prev_hash = row["integrity_hash"]

    return (len(errors) == 0, errors)


# --- School CRUD ---


def create_school(school: School) -> School:
    """学校を作成する"""
    _ensure_table()
    school.updated_at = datetime.now().isoformat()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            schools.insert().values(
                id=school.id,
                name=school.name,
                slug=school.slug,
                retention_days=school.retention_days,
                created_at=school.created_at,
                updated_at=school.updated_at,
            )
        )
    return school


def get_school(school_id: str) -> School | None:
    """学校IDから学校を取得する"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(schools).where(schools.c.id == school_id)
        ).mappings().fetchone()
    if row is None:
        return None
    return School(**dict(row))


def get_school_by_slug(slug: str) -> School | None:
    """スラグから学校を取得する"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(schools).where(schools.c.slug == slug)
        ).mappings().fetchone()
    if row is None:
        return None
    return School(**dict(row))


# --- User CRUD ---


def create_user(user: User) -> User:
    """ユーザーを作成する"""
    _ensure_table()
    user.updated_at = datetime.now().isoformat()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            users.insert().values(
                id=user.id,
                school_id=user.school_id,
                email=user.email,
                hashed_password=user.hashed_password,
                display_name=user.display_name,
                role=user.role,
                is_active=user.is_active,
                mfa_secret=user.mfa_secret,
                mfa_enabled=user.mfa_enabled,
                mfa_backup_codes=user.mfa_backup_codes,
                token_invalidated_at=user.token_invalidated_at,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
    return user


def _decrypt_user_fields(data: dict) -> dict:
    """ユーザーデータの暗号化フィールドを復号する。

    暗号化が有効で復号に失敗した場合は DecryptionError を送出する。
    暗号化が無効の場合は値をそのまま返す（開発モード互換）。
    """
    encryption_on = is_encryption_enabled()
    # MFAシークレットの復号
    if data.get("mfa_secret"):
        decrypted = decrypt_text(data["mfa_secret"])
        if decrypted is not None:
            data["mfa_secret"] = decrypted
        elif encryption_on:
            raise DecryptionError(
                "MFAシークレットの復号に失敗しました。暗号化キーが正しいか確認してください。"
            )
    # バックアップコードの復号（ハッシュ化前の旧データとの互換性）
    if data.get("mfa_backup_codes"):
        decrypted = decrypt_text(data["mfa_backup_codes"])
        if decrypted is not None:
            data["mfa_backup_codes"] = decrypted
        elif encryption_on:
            raise DecryptionError(
                "MFAバックアップコードの復号に失敗しました。暗号化キーが正しいか確認してください。"
            )
    return data


def get_user(user_id: str) -> User | None:
    """ユーザーIDからユーザーを取得する"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(users).where(users.c.id == user_id)
        ).mappings().fetchone()
    if row is None:
        return None
    data = _decrypt_user_fields(dict(row))
    return User(**data)


def get_user_by_email(email: str) -> User | None:
    """メールアドレスからユーザーを取得する"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(users).where(users.c.email == email)
        ).mappings().fetchone()
    if row is None:
        return None
    data = _decrypt_user_fields(dict(row))
    return User(**data)


# --- Password ---


def change_password(user_id: str, new_hashed_password: str) -> bool:
    """パスワードを変更し、既存トークンを全て無効化する。

    Returns:
        更新できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                hashed_password=new_hashed_password,
                updated_at=datetime.now().isoformat(),
            )
        )
    if result.rowcount > 0:
        invalidate_all_tokens(user_id)
    return result.rowcount > 0


# --- MFA ---


def setup_mfa(user_id: str, mfa_secret: str) -> bool:
    """MFAシークレットをユーザーに設定する（まだ有効化しない）。

    暗号化が有効な場合、シークレットは暗号化して保存する。

    Returns:
        更新できたらTrue
    """
    _ensure_table()
    stored_secret = encrypt_text(mfa_secret) or mfa_secret
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                mfa_secret=stored_secret,
                updated_at=datetime.now().isoformat(),
            )
        )
    return result.rowcount > 0


def enable_mfa(user_id: str) -> list[str] | None:
    """MFAを有効化し、バックアップコードを生成・保存する。

    平文コードをユーザーに返し、DBにはハッシュ化したコードを保存する。

    Returns:
        バックアップコード（平文）のリスト。ユーザーが見つからなければNone。
    """
    _ensure_table()
    backup_codes = generate_backup_codes()
    hashed_codes = hash_backup_codes(backup_codes)
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                mfa_enabled=True,
                mfa_backup_codes=encrypt_text(json.dumps(hashed_codes)) or json.dumps(hashed_codes),
                updated_at=datetime.now().isoformat(),
            )
        )
    if result.rowcount == 0:
        return None
    invalidate_all_tokens(user_id)
    return backup_codes


def disable_mfa(user_id: str) -> bool:
    """MFAを無効化し、シークレットとバックアップコードをクリアする。

    Returns:
        更新できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                mfa_enabled=False,
                mfa_secret=None,
                mfa_backup_codes=None,
                updated_at=datetime.now().isoformat(),
            )
        )
    if result.rowcount > 0:
        invalidate_all_tokens(user_id)
    return result.rowcount > 0


def invalidate_all_tokens(user_id: str) -> bool:
    """ユーザーの全トークンを無効化する（token_invalidated_at を現在時刻に設定）。

    パスワード変更・MFA有効化/無効化時に呼び出し、
    既存のアクセストークン・リフレッシュトークンを一括失効させる。

    Returns:
        更新できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                token_invalidated_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
        )
    # DB上のリフレッシュトークンも全て revoke
    revoke_all_user_refresh_tokens(user_id)
    return result.rowcount > 0


def update_mfa_backup_codes(user_id: str, codes_json: str) -> bool:
    """バックアップコードを更新する（使用済みコード除去後の保存用）。

    Returns:
        更新できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                mfa_backup_codes=encrypt_text(codes_json) or codes_json,
                updated_at=datetime.now().isoformat(),
            )
        )
    return result.rowcount > 0


# --- Refresh Token Family ---


def store_refresh_token(
    jti: str, user_id: str, family_id: str, expires_at: str,
) -> None:
    """リフレッシュトークンをDBに記録する。"""
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            refresh_tokens.insert().values(
                jti=jti,
                user_id=user_id,
                family_id=family_id,
                revoked=False,
                created_at=datetime.now().isoformat(),
                expires_at=expires_at,
            )
        )


def use_refresh_token(jti: str) -> dict | None:
    """リフレッシュトークンを使用する（ローテーション）。

    - 有効なトークンなら revoke して情報を返す
    - 既に revoke 済み（再利用攻撃）なら family 全体を revoke して None
    - 存在しないなら None

    Returns:
        トークン情報の dict、または None（拒否時）
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sa.select(refresh_tokens).where(refresh_tokens.c.jti == jti)
        ).mappings().fetchone()

        if row is None:
            return None

        if row["revoked"]:
            # 再利用検知: ファミリー全体を revoke（盗難対策）
            conn.execute(
                refresh_tokens.update()
                .where(refresh_tokens.c.family_id == row["family_id"])
                .values(revoked=True)
            )
            return None

        # 正常: このトークンを revoke
        conn.execute(
            refresh_tokens.update()
            .where(refresh_tokens.c.jti == jti)
            .values(revoked=True)
        )

    return dict(row)


def revoke_family(family_id: str) -> int:
    """トークンファミリー全体を revoke する。

    Returns:
        revoke した件数
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            refresh_tokens.update()
            .where(refresh_tokens.c.family_id == family_id)
            .where(refresh_tokens.c.revoked == False)  # noqa: E712
            .values(revoked=True)
        )
    return result.rowcount


def revoke_all_user_refresh_tokens(user_id: str) -> int:
    """ユーザーの全リフレッシュトークンを revoke する。

    Returns:
        revoke した件数
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            refresh_tokens.update()
            .where(refresh_tokens.c.user_id == user_id)
            .where(refresh_tokens.c.revoked == False)  # noqa: E712
            .values(revoked=True)
        )
    return result.rowcount


def cleanup_expired_refresh_tokens() -> int:
    """期限切れのリフレッシュトークンを削除する。

    Returns:
        削除した件数
    """
    _ensure_table()
    engine = get_engine()
    now = datetime.now().isoformat()
    with engine.begin() as conn:
        result = conn.execute(
            refresh_tokens.delete().where(refresh_tokens.c.expires_at < now)
        )
    return result.rowcount


# --- API Key Management (KMS) ---

VALID_PROVIDERS = ('gemini', 'anthropic')


def save_api_key(
    school_id: str,
    provider: str,
    api_key: str,
    created_by: str | None = None,
) -> dict:
    """APIキーを暗号化して保存する（UPSERT）。

    Args:
        school_id: 学校ID
        provider: プロバイダー名 ("gemini" | "anthropic")
        api_key: 平文のAPIキー
        created_by: 操作ユーザーID

    Returns:
        保存結果の概要（id, provider, key_suffix）
    """
    if provider not in VALID_PROVIDERS:
        raise ValueError(f'未対応のproviderです: {provider}')

    _ensure_table()
    encrypted = encrypt_text(api_key)
    if encrypted is None:
        # 暗号化無効時は平文保存（開発モード）。本番では ENCRYPTION_KEY 必須
        encrypted = api_key

    now = datetime.now().isoformat()
    key_suffix = api_key[-4:] if len(api_key) >= 4 else api_key

    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            sa.select(api_keys.c.id).where(
                api_keys.c.school_id == school_id,
                api_keys.c.provider == provider,
            )
        ).fetchone()

        if existing:
            conn.execute(
                api_keys.update()
                .where(api_keys.c.id == existing[0])
                .values(
                    encrypted_key=encrypted,
                    key_suffix=key_suffix,
                    created_by=created_by,
                    updated_at=now,
                )
            )
            key_id = existing[0]
        else:
            key_id = str(uuid.uuid4())
            conn.execute(
                api_keys.insert().values(
                    id=key_id,
                    school_id=school_id,
                    provider=provider,
                    encrypted_key=encrypted,
                    key_suffix=key_suffix,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )

    log_audit_event(
        action="set_api_key",
        resource_type="api_key",
        resource_id=provider,
        user_id=created_by,
        school_id=school_id,
        details={"provider": provider, "key_suffix": key_suffix},
    )

    return {"id": key_id, "provider": provider, "key_suffix": key_suffix}


def get_api_key(school_id: str, provider: str) -> str | None:
    """学校のAPIキーを復号して返す。未設定ならNone。"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(api_keys.c.encrypted_key).where(
                api_keys.c.school_id == school_id,
                api_keys.c.provider == provider,
            )
        ).fetchone()

    if row is None:
        return None

    decrypted = decrypt_text(row[0])
    if decrypted is not None:
        return decrypted
    if is_encryption_enabled():
        # 暗号化が有効なのに復号できない = 鍵不一致
        raise DecryptionError(
            f"{provider} APIキーの復号に失敗しました。暗号化キーが正しいか確認してください。"
        )
    # 暗号化無効時（開発モード）は平文がそのまま入っている
    return row[0]


def list_api_keys(school_id: str) -> list[dict]:
    """学校に設定されているAPIキーの一覧を返す（キー本体は含まない）。"""
    _ensure_table()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                api_keys.c.id,
                api_keys.c.provider,
                api_keys.c.key_suffix,
                api_keys.c.created_at,
                api_keys.c.updated_at,
            ).where(api_keys.c.school_id == school_id)
            .order_by(api_keys.c.provider)
        ).mappings().all()

    return [dict(r) for r in rows]


def delete_api_key(school_id: str, provider: str, user_id: str | None = None) -> bool:
    """学校のAPIキーを削除する。

    Returns:
        削除できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            api_keys.delete().where(
                api_keys.c.school_id == school_id,
                api_keys.c.provider == provider,
            )
        )

    deleted = result.rowcount > 0
    if deleted:
        log_audit_event(
            action="delete_api_key",
            resource_type="api_key",
            resource_id=provider,
            user_id=user_id,
            school_id=school_id,
            details={"provider": provider},
        )
    return deleted

# --- Scoring Session CRUD ---


class DecryptionError(Exception):
    """暗号化カラムが存在するが復号に失敗した場合のエラー。"""


def _decrypt_session_column(row: dict, col: str) -> list:
    """セッション行から students / ocr_results を復号して返す。

    暗号化カラムに値がある場合は復号を試み、失敗したら DecryptionError を投げる。
    暗号化カラムが空/NULL の場合は平文カラムにフォールバックする（旧データ互換）。
    """
    encrypted_col = f"{col}_encrypted"
    encrypted_val = row.get(encrypted_col)

    if encrypted_val:
        decrypted = decrypt_json(encrypted_val)
        if decrypted is not None:
            return decrypted
        # 暗号化カラムに値があるのに復号できない = 鍵不一致 or データ破損
        raise DecryptionError(
            f"{col} の復号に失敗しました。暗号化キーが正しいか確認してください。"
        )

    # 暗号化カラムが空 = 暗号化前の旧データ → 平文フォールバック
    plain_val = row.get(col, "[]")
    if isinstance(plain_val, str):
        return json.loads(plain_val)
    return plain_val if plain_val is not None else []


def save_session(
    session: ScoringSession,
    school_id: str | None = None,
    created_by: str | None = None,
) -> Path:
    """採点セッションをDBに保存する"""
    ensure_dirs()
    _ensure_table()
    session.updated_at = datetime.now().isoformat()
    if school_id is not None:
        session.school_id = school_id
    if created_by is not None:
        session.created_by = created_by
    data = session.to_dict()

    engine = get_engine()
    with engine.begin() as conn:
        # UPSERT: 存在チェック → insert or update
        existing = conn.execute(
            sa.select(scoring_sessions.c.session_id).where(
                scoring_sessions.c.session_id == session.session_id
            )
        ).fetchone()

        # 暗号化が有効なら機密カラムを暗号化保存し、平文カラムは空値にする
        students_encrypted = encrypt_json(data["students"])
        ocr_results_encrypted = encrypt_json(data["ocr_results"])

        # 暗号化成功時は平文カラムに空値を書く（データ二重保存を防止）
        if students_encrypted is not None:
            students_plain = "[]"
            ocr_results_plain = "[]"
        else:
            students_plain = data["students"]
            ocr_results_plain = data["ocr_results"]

        values = dict(
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            rubric_title=data["rubric_title"],
            pdf_filename=data["pdf_filename"],
            pages_per_student=data["pages_per_student"],
            grading_mode=data["grading_mode"],
            students=students_plain,
            ocr_results=ocr_results_plain,
            students_encrypted=students_encrypted,
            ocr_results_encrypted=ocr_results_encrypted,
            school_id=data.get("school_id"),
            created_by=data.get("created_by"),
        )

        if existing:
            conn.execute(
                scoring_sessions.update()
                .where(scoring_sessions.c.session_id == session.session_id)
                .values(**values)
            )
        else:
            conn.execute(
                scoring_sessions.insert().values(
                    session_id=data["session_id"],
                    **values,
                )
            )

    # 互換のため合成パスを返す（呼び出し側は使っていない）
    return Path(f"db://session_{session.session_id}")


def load_session(session_id: str, school_id: str | None = None) -> ScoringSession | None:
    """セッションIDからセッションを読み込む。school_id指定時はテナント検証。"""
    _ensure_table()
    engine = get_engine()
    query = sa.select(scoring_sessions).where(
        scoring_sessions.c.session_id == session_id
    )
    if school_id is not None:
        query = query.where(scoring_sessions.c.school_id == school_id)
    with engine.connect() as conn:
        row = conn.execute(query).mappings().fetchone()

    if row is None:
        return None

    data = dict(row)

    # 共通ヘルパーで復号（暗号化カラムがあるのに復号失敗なら DecryptionError）
    for col in ("students", "ocr_results"):
        data[col] = _decrypt_session_column(data, col)

    # 暗号化カラムを from_dict に渡さない
    data.pop("students_encrypted", None)
    data.pop("ocr_results_encrypted", None)

    return ScoringSession.from_dict(data)


def list_sessions(school_id: str | None = None) -> list[dict]:
    """保存済みセッションの一覧を返す。school_id指定時はテナントフィルタ。

    暗号化有効時は暗号化カラムから復号して件数を取得する。
    """
    _ensure_table()
    engine = get_engine()
    query = sa.select(
        scoring_sessions.c.session_id,
        scoring_sessions.c.created_at,
        scoring_sessions.c.rubric_title,
        scoring_sessions.c.pdf_filename,
        scoring_sessions.c.students,
        scoring_sessions.c.students_encrypted,
    ).order_by(scoring_sessions.c.created_at.desc())
    if school_id is not None:
        query = query.where(scoring_sessions.c.school_id == school_id)
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    sessions = []
    for row in rows:
        students = _decrypt_session_column(row, "students")
        sessions.append({
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "rubric_title": row["rubric_title"],
            "pdf_filename": row["pdf_filename"],
            "student_count": len(students),
        })
    return sessions


def _sanitize_csv_cell(value) -> str:
    """Excelの数式インジェクションを防止する。"""
    s = str(value) if value is not None else ""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def export_csv(session: ScoringSession) -> str:
    """採点結果をCSV文字列にエクスポートする"""
    output = io.StringIO()
    writer = csv.writer(output)

    # ヘッダー行
    headers = ["学生番号", "氏名", "状態"]
    # 設問ごとの列を動的に作成
    if session.students:
        first = session.students[0]
        for qs in first.question_scores:
            headers.extend([
                f"問{qs.question_id}_得点",
                f"問{qs.question_id}_配点",
                f"問{qs.question_id}_読取",
                f"問{qs.question_id}_コメント",
                f"問{qs.question_id}_フィードバック",
                f"問{qs.question_id}_確信度",
                f"問{qs.question_id}_要確認",
                f"問{qs.question_id}_確認理由",
            ])
    headers.extend(["合計点", "満点", "教員メモ"])
    writer.writerow(headers)

    # データ行
    for student in session.students:
        row = [_sanitize_csv_cell(student.student_id),
               _sanitize_csv_cell(student.student_name), student.status]
        for qs in student.question_scores:
            row.extend([
                qs.score,
                qs.max_points,
                _sanitize_csv_cell(qs.transcribed_text),
                _sanitize_csv_cell(qs.comment),
                _sanitize_csv_cell(qs.feedback) if hasattr(qs, "feedback") else "",
                qs.confidence,
                "要確認" if qs.needs_review else "",
                _sanitize_csv_cell(qs.review_reason) if qs.needs_review else "",
            ])
        row.extend([student.total_score, student.total_max_points,
                     _sanitize_csv_cell(student.reviewer_notes)])
        writer.writerow(row)

    return output.getvalue()


def export_csv_file(session: ScoringSession) -> Path:
    """採点結果をCSVファイルにエクスポートする"""
    ensure_dirs()
    csv_content = export_csv(session)
    filename = f"results_{session.session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8-sig") as f:  # BOM付きUTF-8（Excel対応）
        f.write(csv_content)
    return path


def migrate_json_to_db(data_dir: Path | None = None) -> list[str]:
    """既存のJSONファイルをDBに移行する。移行済みファイルは .json.migrated にリネーム。

    Returns:
        移行したセッションIDのリスト
    """
    _ensure_table()
    if data_dir is None:
        data_dir = Path(__file__).parent / "data"

    migrated = []
    for path in sorted(data_dir.glob("session_*.json")):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        session = ScoringSession.from_dict(data)
        save_session(session)
        path.rename(path.with_suffix(".json.migrated"))
        migrated.append(session.session_id)

    return migrated


def seed_admin_user() -> tuple[School, User]:
    """環境変数から初期管理者ユーザーを作成する（冪等）。

    環境変数:
        ADMIN_EMAIL: 管理者メールアドレス（必須）
        ADMIN_PASSWORD: 管理者パスワード（必須）

    未設定の場合は ValueError を送出する。
    """
    import os

    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        raise ValueError(
            "ADMIN_EMAIL と ADMIN_PASSWORD を環境変数に設定してください。"
            "デフォルト認証情報での起動はセキュリティ上許可されていません。"
        )
    school_slug = "default"

    # 学校がなければ作成
    school = get_school_by_slug(school_slug)
    if school is None:
        school = School(name="デフォルト学校", slug=school_slug)
        create_school(school)

    # ユーザーがなければ作成
    user = get_user_by_email(email)
    if user is None:
        user = User(
            school_id=school.id,
            email=email,
            hashed_password=hash_password(password),
            display_name="管理者",
            role="admin",
        )
        create_user(user)

    return school, user


# --- Session Deletion & Retention ---


def delete_session(
    session_id: str,
    school_id: str | None = None,
    user_id: str | None = None,
) -> bool:
    """セッションを削除する。テナント検証付き。

    Returns:
        削除できたらTrue
    """
    _ensure_table()
    engine = get_engine()
    query = scoring_sessions.delete().where(
        scoring_sessions.c.session_id == session_id
    )
    if school_id is not None:
        query = query.where(scoring_sessions.c.school_id == school_id)

    with engine.begin() as conn:
        result = conn.execute(query)

    deleted = result.rowcount > 0
    if deleted:
        log_audit_event(
            action="delete",
            resource_type="session",
            resource_id=session_id,
            user_id=user_id,
            school_id=school_id,
        )
    return deleted


def purge_expired_sessions(
    school_id: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """保存期間を超えたセッションを一括削除する。

    school_id 指定時はその学校のみ対象。未指定時は全校対象。
    各学校の retention_days に基づき、期限切れセッションを削除する。
    school_id が NULL のセッション（レガシー）はデフォルト365日。

    Args:
        school_id: 対象学校ID（未指定時は全校）
        user_id: 実行者のユーザーID（監査ログに記録）

    Returns:
        削除したセッションIDのリスト
    """
    _ensure_table()
    engine = get_engine()
    now = datetime.now()
    purged = []

    with engine.begin() as conn:
        # 学校ごとの retention_days を取得
        school_query = sa.select(schools.c.id, schools.c.retention_days)
        if school_id is not None:
            school_query = school_query.where(schools.c.id == school_id)
        school_rows = conn.execute(school_query).mappings().all()

        for school_row in school_rows:
            cutoff = (
                now - __import__("datetime").timedelta(days=school_row["retention_days"])
            ).isoformat()
            rows = conn.execute(
                sa.select(scoring_sessions.c.session_id).where(
                    scoring_sessions.c.school_id == school_row["id"],
                    scoring_sessions.c.updated_at < cutoff,
                    scoring_sessions.c.updated_at != "",
                )
            ).fetchall()
            session_ids = [r[0] for r in rows]

            if session_ids:
                conn.execute(
                    scoring_sessions.delete().where(
                        scoring_sessions.c.session_id.in_(session_ids)
                    )
                )
                purged.extend(session_ids)

        # school_id が NULL のセッション（レガシー）はデフォルト365日
        # school_id 指定時はレガシーセッションはスキップ
        if school_id is None:
            cutoff_default = (now - __import__("datetime").timedelta(days=365)).isoformat()
            rows = conn.execute(
                sa.select(scoring_sessions.c.session_id).where(
                    scoring_sessions.c.school_id.is_(None),
                    scoring_sessions.c.updated_at < cutoff_default,
                    scoring_sessions.c.updated_at != "",
                )
            ).fetchall()
            legacy_ids = [r[0] for r in rows]
            if legacy_ids:
                conn.execute(
                    scoring_sessions.delete().where(
                        scoring_sessions.c.session_id.in_(legacy_ids)
                    )
                )
                purged.extend(legacy_ids)

    if purged:
        log_audit_event(
            action="purge_expired",
            resource_type="session",
            user_id=user_id,
            details={"count": len(purged), "session_ids": purged},
        )

    return purged


def export_school_data(school_id: str, user_id: str | None = None) -> dict:
    """学校の全データをエクスポートする（解約・データポータビリティ用）。

    Returns:
        学校情報、ユーザー一覧、セッション一覧を含むdict
    """
    _ensure_table()
    engine = get_engine()

    with engine.connect() as conn:
        # 学校情報
        school_row = conn.execute(
            sa.select(schools).where(schools.c.id == school_id)
        ).mappings().fetchone()
        if school_row is None:
            return {"error": "school not found"}

        # ユーザー一覧（パスワードハッシュは除外）
        user_rows = conn.execute(
            sa.select(
                users.c.id, users.c.email, users.c.display_name,
                users.c.role, users.c.is_active, users.c.created_at,
            ).where(users.c.school_id == school_id)
        ).mappings().all()

        # セッション一覧
        session_rows = conn.execute(
            sa.select(scoring_sessions).where(
                scoring_sessions.c.school_id == school_id
            )
        ).mappings().all()

        sessions = []
        for row in session_rows:
            data = dict(row)
            # 共通ヘルパーで復号（暗号化ONでも正しくデータを取得）
            for col in ("students", "ocr_results"):
                data[col] = _decrypt_session_column(data, col)
            data.pop("students_encrypted", None)
            data.pop("ocr_results_encrypted", None)
            sessions.append(data)

    log_audit_event(
        action="export_school_data",
        resource_type="school",
        resource_id=school_id,
        school_id=school_id,
        user_id=user_id,
        details={"user_count": len(user_rows), "session_count": len(sessions)},
    )

    return {
        "school": dict(school_row),
        "users": [dict(r) for r in user_rows],
        "sessions": sessions,
        "exported_at": datetime.now().isoformat(),
    }


def delete_school_data(school_id: str, user_id: str | None = None) -> dict:
    """学校のデータを削除する（解約時）。

    セッション、APIキー、ユーザー、学校レコードを削除する。
    監査ログは規制遵守のため削除せず、個人情報（email等）を匿名化して保持する。

    Returns:
        削除・匿名化した件数の概要
    """
    _ensure_table()
    engine = get_engine()

    with engine.begin() as conn:
        # セッション削除
        session_result = conn.execute(
            scoring_sessions.delete().where(
                scoring_sessions.c.school_id == school_id
            )
        )
        # APIキー削除
        api_keys_result = conn.execute(
            api_keys.delete().where(
                api_keys.c.school_id == school_id
            )
        )
        # ユーザー削除
        user_result = conn.execute(
            users.delete().where(users.c.school_id == school_id)
        )
        # 学校削除
        school_result = conn.execute(
            schools.delete().where(schools.c.id == school_id)
        )
        # 監査ログの個人情報を匿名化（ログ自体は規制遵守のため保持）
        audit_result = conn.execute(
            audit_logs.update()
            .where(audit_logs.c.school_id == school_id)
            .values(
                user_id=None,
                details=None,
                ip_address=None,
            )
        )

    summary = {
        "sessions_deleted": session_result.rowcount,
        "api_keys_deleted": api_keys_result.rowcount,
        "users_deleted": user_result.rowcount,
        "school_deleted": school_result.rowcount,
        "audit_logs_anonymized": audit_result.rowcount,
    }

    log_audit_event(
        action="delete_school_data",
        resource_type="school",
        resource_id=school_id,
        user_id=user_id,
        school_id=school_id,
        details=summary,
    )

    return summary


if __name__ == "__main__":
    import sys
    from config import validate_secrets
    validate_secrets()

    if len(sys.argv) > 1 and sys.argv[1] == "migrate-json":
        ids = migrate_json_to_db()
        print(f"移行完了: {len(ids)} セッション")
        for sid in ids:
            print(f"  - {sid}")
    elif len(sys.argv) > 1 and sys.argv[1] == "seed-admin":
        school, user = seed_admin_user()
        print(f"学校: {school.name} ({school.slug})")
        print(f"管理者: {user.email} ({user.display_name})")
    else:
        print("使い方:")
        print("  python -m storage migrate-json")
        print("  python -m storage seed-admin")

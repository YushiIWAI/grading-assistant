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
from db import api_keys, audit_logs, get_engine, init_db, schools, scoring_sessions, users
from encryption import decrypt_json, decrypt_text, encrypt_json, encrypt_text, is_encryption_enabled
from models import School, ScoringSession, User

# 監査ログのHMAC鍵（JWT_SECRET_KEYを流用、未設定時はフォールバック）
_AUDIT_HMAC_KEY = os.environ.get("JWT_SECRET_KEY", "dev-audit-key").encode()

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
    user_id: str | None = None,
    school_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> str:
    """HMACベースの改ざん検知ハッシュを計算する。

    署名対象: log_id, timestamp, action, resource_type, resource_id, prev_hash,
              user_id, school_id, details（正規化JSON）, ip_address
    """
    details_str = json.dumps(details, sort_keys=True, ensure_ascii=False) if details else ""
    message = (
        f"{log_id}|{timestamp}|{action}|{resource_type}|{resource_id or ''}"
        f"|{prev_hash}|{user_id or ''}|{school_id or ''}|{details_str}|{ip_address or ''}"
    )
    return hmac.new(_AUDIT_HMAC_KEY, message.encode(), hashlib.sha256).hexdigest()


def _get_latest_audit_hash(conn=None) -> str:
    """最新の監査ログのintegrity_hashを取得する。チェーンの起点は空文字列。

    conn が渡された場合はそのコネクション内で実行する（トランザクション統合用）。
    """
    if conn is not None:
        row = conn.execute(
            sa.select(audit_logs.c.integrity_hash)
            .order_by(audit_logs.c.timestamp.desc())
            .limit(1)
        ).fetchone()
        return row[0] if row else ""

    engine = get_engine()
    with engine.connect() as c:
        row = c.execute(
            sa.select(audit_logs.c.integrity_hash)
            .order_by(audit_logs.c.timestamp.desc())
            .limit(1)
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

    prev_hash取得とinsertを単一トランザクション内で実行し、
    並行書き込みによるチェーン断絶を防止する。

    Returns:
        作成したログエントリのID
    """
    _ensure_table()
    log_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    engine = get_engine()
    with engine.begin() as conn:
        prev_hash = _get_latest_audit_hash(conn)
        integrity_hash = _compute_audit_hash(
            log_id, timestamp, action, resource_type, resource_id, prev_hash,
            user_id=user_id, school_id=school_id, details=details, ip_address=ip_address,
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
            )
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


def verify_audit_chain(page_size: int = 1000) -> tuple[bool, list[str]]:
    """監査ログチェーンの整合性を全件検証する。

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
            batch = conn.execute(
                sa.select(audit_logs)
                .order_by(audit_logs.c.timestamp.asc())
                .limit(page_size)
                .offset(offset)
            ).mappings().all()
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

    errors = []
    prev_hash = ""
    for row in rows:
        # details が文字列ならdictに変換（DB格納時にJSON文字列化されている場合）
        details = row.get("details")
        if isinstance(details, str):
            details = json.loads(details) if details else None
        expected = _compute_audit_hash(
            row["id"], row["timestamp"], row["action"],
            row["resource_type"], row["resource_id"], row["prev_hash"],
            user_id=row.get("user_id"),
            school_id=row.get("school_id"),
            details=details,
            ip_address=row.get("ip_address"),
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
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
    return user


def _decrypt_user_fields(data: dict) -> dict:
    """ユーザーデータの暗号化フィールドを復号する。"""
    # MFAシークレットの復号
    if data.get("mfa_secret"):
        decrypted = decrypt_text(data["mfa_secret"])
        if decrypted is not None:
            data["mfa_secret"] = decrypted
    # バックアップコードの復号（ハッシュ化前の旧データとの互換性）
    if data.get("mfa_backup_codes"):
        decrypted = decrypt_text(data["mfa_backup_codes"])
        if decrypted is not None:
            data["mfa_backup_codes"] = decrypted
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
                mfa_backup_codes=json.dumps(hashed_codes),
                updated_at=datetime.now().isoformat(),
            )
        )
    if result.rowcount == 0:
        return None
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
                mfa_backup_codes=codes_json,
                updated_at=datetime.now().isoformat(),
            )
        )
    return result.rowcount > 0




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

    # 暗号化カラムがあれば復号を優先、なければ平文カラムを使用
    for col in ("students", "ocr_results"):
        encrypted_col = f"{col}_encrypted"
        decrypted = None
        if data.get(encrypted_col):
            decrypted = decrypt_json(data[encrypted_col])
        if decrypted is not None:
            data[col] = decrypted
        elif isinstance(data[col], str):
            data[col] = json.loads(data[col])

    # 暗号化カラムを from_dict に渡さない
    data.pop("students_encrypted", None)
    data.pop("ocr_results_encrypted", None)

    return ScoringSession.from_dict(data)


def list_sessions(school_id: str | None = None) -> list[dict]:
    """保存済みセッションの一覧を返す。school_id指定時はテナントフィルタ。"""
    _ensure_table()
    engine = get_engine()
    query = sa.select(
        scoring_sessions.c.session_id,
        scoring_sessions.c.created_at,
        scoring_sessions.c.rubric_title,
        scoring_sessions.c.pdf_filename,
        scoring_sessions.c.students,
    ).order_by(scoring_sessions.c.created_at.desc())
    if school_id is not None:
        query = query.where(scoring_sessions.c.school_id == school_id)
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    sessions = []
    for row in rows:
        students = row["students"]
        if isinstance(students, str):
            students = json.loads(students)
        sessions.append({
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "rubric_title": row["rubric_title"],
            "pdf_filename": row["pdf_filename"],
            "student_count": len(students),
        })
    return sessions


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
                f"問{qs.question_id}_確信度",
                f"問{qs.question_id}_要確認",
                f"問{qs.question_id}_確認理由",
            ])
    headers.extend(["合計点", "満点", "教員メモ"])
    writer.writerow(headers)

    # データ行
    for student in session.students:
        row = [student.student_id, student.student_name, student.status]
        for qs in student.question_scores:
            row.extend([
                qs.score,
                qs.max_points,
                qs.transcribed_text,
                qs.comment,
                qs.confidence,
                "要確認" if qs.needs_review else "",
                qs.review_reason if qs.needs_review else "",
            ])
        row.extend([student.total_score, student.total_max_points, student.reviewer_notes])
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
        ADMIN_EMAIL: 管理者メールアドレス（デフォルト: admin@example.com）
        ADMIN_PASSWORD: 管理者パスワード（デフォルト: changeme）
    """
    import os

    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("ADMIN_PASSWORD", "changeme")
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


def purge_expired_sessions() -> list[str]:
    """保存期間を超えたセッションを一括削除する。

    各学校の retention_days に基づき、期限切れセッションを削除する。
    school_id が NULL のセッションはデフォルト365日。

    Returns:
        削除したセッションIDのリスト
    """
    _ensure_table()
    engine = get_engine()
    now = datetime.now()
    purged = []

    with engine.begin() as conn:
        # 学校ごとの retention_days を取得
        school_rows = conn.execute(
            sa.select(schools.c.id, schools.c.retention_days)
        ).mappings().all()

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
            details={"count": len(purged), "session_ids": purged},
        )

    return purged


def export_school_data(school_id: str) -> dict:
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
            for col in ("students", "ocr_results"):
                if isinstance(data[col], str):
                    data[col] = json.loads(data[col])
            # 暗号化カラムは除外（平文カラムで出力）
            data.pop("students_encrypted", None)
            data.pop("ocr_results_encrypted", None)
            sessions.append(data)

    log_audit_event(
        action="export_school_data",
        resource_type="school",
        resource_id=school_id,
        school_id=school_id,
        details={"user_count": len(user_rows), "session_count": len(sessions)},
    )

    return {
        "school": dict(school_row),
        "users": [dict(r) for r in user_rows],
        "sessions": sessions,
        "exported_at": datetime.now().isoformat(),
    }


def delete_school_data(school_id: str, user_id: str | None = None) -> dict:
    """学校の全データを完全削除する（解約時）。

    Returns:
        削除した件数の概要
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

    summary = {
        "sessions_deleted": session_result.rowcount,
        "api_keys_deleted": api_keys_result.rowcount,
        "users_deleted": user_result.rowcount,
        "school_deleted": school_result.rowcount,
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

---
description: 採点補助アプリ (grading-assistant) の開発に関するタスク全般。機能追加、バグ修正、UI改善、採点ロジック調整など。
---

# Grading Assistant — プロジェクトコンテキスト

## 概要
国語の試験採点を支援するStreamlitアプリ。手書き答案のPDFをAI（Gemini/Claude）でOCR・仮採点し、教師の採点作業を補助する。
AI採点はあくまで参考値であり、最終判断は教師が行う設計思想。

## 技術スタック
- 言語: Python 3
- UI: Streamlit (>=1.33.0)
- API: FastAPI (v0.2.0) — 認証・セッション・OCR・採点の全エンドポイント
- DB: SQLAlchemy Core + Alembic — PostgreSQL（本番）/ SQLite（テスト・開発）
- 認証: 自前 JWT（PyJWT + bcrypt）— アクセストークン + リフレッシュトークン
- AI: Google Gemini API (google-genai, 推奨) / Anthropic Claude API (anthropic, オプション)
- PDF処理: PyMuPDF (>=1.24.0)
- 画像処理: Pillow (>=10.0.0)
- 設定: PyYAML (>=6.0) — ルーブリック定義
- 環境変数: python-dotenv
- テスト: pytest (>=8.0.0) — 157テスト
- バージョン管理: Git（mainブランチ）
- インフラ: Docker Compose（PostgreSQL 16 Alpine）

## ディレクトリ構成
```
grading-assistant/
├── app.py                # メインStreamlitアプリ（JWT認証 or レガシーパスワード）
├── api_client.py         # Streamlit → API クライアント（認証トークン付き）
├── auth.py               # 認証ユーティリティ（bcrypt + JWT）
├── rubric_io.py          # ルーブリック変換・YAML入出力の共通モジュール
├── models.py             # データモデル（Rubric, Question, School, User, ScoringSession等）
├── scoring_engine.py     # 採点エンジン（マルチAPI対応）
├── pdf_processor.py      # PDF→画像変換・処理
├── provider_factory.py   # プロバイダ構築（APIキー未設定時のDemoフォールバック）
├── encryption.py         # 保存時暗号化（Fernet対称暗号、ENCRYPTION_KEY環境変数）
├── storage.py            # DB永続化（セッション・School・User CRUD・テナント分離・監査ログ・削除・パージ）
├── db.py                 # SQLAlchemy Core テーブル定義・エンジン管理（audit_logs含む）
├── api/
│   ├── app.py            # FastAPI（認証・セッション・OCR・採点エンドポイント）
│   └── deps.py           # FastAPI 認証依存関係（CurrentUser, get_optional_user）
├── alembic/              # DBマイグレーション
├── docker-compose.yml    # PostgreSQL 16 Alpine
├── requirements.txt      # 依存パッケージ
├── .env.example          # 環境変数テンプレート（DB・JWT・APIキー・暗号化鍵）
├── docs/
│   ├── adr/              # アーキテクチャ判断記録
│   └── privacy-policy-draft.md  # プライバシーポリシードラフト
├── rubrics/              # 採点ルーブリック定義（YAML）
├── tests/                # テストスイート（pytest, 157テスト）
│   ├── conftest.py       # 共有フィクスチャ（test_db, test_school, test_user, auth_headers）
│   ├── test_models.py
│   ├── test_scoring_engine.py
│   ├── test_storage.py   # School/User CRUD + テナント分離 + 削除/パージ/エクスポートテスト
│   ├── test_auth.py      # パスワードハッシュ・JWT生成/検証テスト
│   ├── test_audit.py     # 監査ログ（記録・検索・チェーン検証）テスト
│   ├── test_encryption.py # 暗号化（Fernet）テスト
│   ├── test_api.py       # 認証 + テナント分離 + 削除/監査ログ/管理者APIテスト
│   └── test_api_client.py
├── data/                 # 保存済み採点セッション（JSON）
├── output/               # エクスポート結果
├── test_data/            # サンプルPDF
├── run.sh / run.bat      # 起動スクリプト
└── setup.sh / setup.bat  # セットアップスクリプト
```

## 主要モジュール

### models.py — データモデル
- `School`: 学校（id, name, slug, created_at, updated_at）— マルチテナントの単位
- `User`: ユーザー（id, school_id, email, hashed_password, display_name, role, is_active）
- `Rubric`: 試験メタデータと問題定義（title, total_points, pages_per_student, questions, notes）
- `Question` / `SubQuestion`: 個別問題（配点・解答タイプ: short_answer / descriptive / selection）
- `StudentResult`: 生徒別採点結果（is_reference フラグで教員採点を参考例としてAIに提供可能）
- `QuestionScore`: 問題別スコア（信頼度・要レビューフラグ・ai_score バックアップ付き）
- `ScoringSession`: 複数生徒を含む採点セッション全体（+ school_id, created_by でテナント紐付け）
  - `from_dict()` / `to_dict()`: JSON永続化用シリアライズ（未知キーは自動フィルタ）
  - `get_reference_students()`: 参考例マーク済み学生の取得
  - `get_ocr_for_student()` / `get_all_answers_for_question()`: OCRデータアクセス
  - `ocr_complete()`: 全学生OCR完了判定
  - `summary()`: 統計サマリー（採点済み数・平均点・要確認数）
- `StudentOcr` / `OcrAnswer`: 手書きOCR結果（問題別読み取りテキスト・信頼度・手動修正フラグ）

### scoring_engine.py — 採点エンジン
- **プロバイダ抽象化** (`ScoringProvider` ABC → `GeminiProvider` / `AnthropicProvider` / `DemoProvider`)
  - GeminiProvider: モデル選択肢 `gemini-3.1-pro-preview`（デフォルト）/ `gemini-2.5-flash` / `gemini-2.5-pro`、120秒タイムアウト（ThreadPoolExecutor）
  - AnthropicProvider: モデル選択肢 `claude-sonnet-4-20250514`（デフォルト）/ `claude-haiku-4-20250414`、120秒タイムアウト
  - DemoProvider: API不要のデモ用ダミー採点
- **RateLimiter**: スライディングウィンドウ方式（Gemini 14RPM, Anthropic 50RPM）
- **スキーマ検証**: `_validate_schema()` + 8つのスキーマ定数（OCR_SCHEMA, SCORING_SCHEMA, HORIZONTAL_SCHEMA, VERIFICATION_SCHEMA 等）、4つのparse関数で使用
- **採点モード**:
  - 設問別採点: `score_student_by_question()` — 1学生ずつ設問単位で採点
  - 横断採点: `run_horizontal_grading()` → `grade_question_horizontally()` — 全学生を問ごとにバッチ一括採点
- **ダブルチェック方式**: `verify_question_scores()` — 記述式問題の2パス検証（`VERIFICATION_BATCH_SIZE=10`）
- **参考例抽出**: `_build_reference_for_question()` — 教員採点済み学生のスコアを設問単位で抽出しAIに提示
- **ユーティリティ**:
  - `_extract_json()`: AIレスポンスからJSON修復・抽出
  - `_api_call_with_retry()`: 全APIコールのリトライ機構
  - `_thinking_budget_for_question()`: 問題タイプに応じたGemini thinking token調整
  - `recommend_batch_size()`: ルーブリック内容からバッチサイズ自動推奨
  - `analyze_batch_calibration()`: バッチ間スコア分布の偏り検出
- **後処理ルール**: 記述式満点→needs_review=True、部分点でhigh→mediumに補正

### pdf_processor.py — PDF→画像変換・処理
- `pdf_to_images()`: PDF→PIL Image変換（DPI指定可能、デフォルト200）
- `split_pages_by_student()`: ページを学生ごとにグループ化（1-indexed）
- `image_to_base64()`: 画像→Base64変換（API送信用、長辺max_size=1600にリサイズ）
- `PrivacyMaskConfig` / `mask_student_name()` / `mask_images_for_external_ai()`: 外部AI送信前に氏名欄をマスキングしたコピーを生成
- `image_to_bytes()`: 画像→bytes変換（Streamlit表示用）
- `get_pdf_page_count()`: PDFのページ数取得（バリデーション用）

### rubric_io.py — ルーブリック共通変換
- `rubric_from_dict()`: dict → `Rubric` 変換
- `load_rubric_from_yaml()`: YAML文字列 → `Rubric`
- `rubric_to_yaml()`: `Rubric` → YAML文字列
- `rubric_summary()`: API / UI向け要約情報

### auth.py — 認証ユーティリティ
- `hash_password(plain) → str`: bcryptハッシュ
- `verify_password(plain, hashed) → bool`: パスワード検証
- `create_access_token(user_id, school_id, role) → str`: JWTアクセストークン（claims: sub, school_id, role, type="access", exp, iat）
- `create_refresh_token(user_id) → str`: JWTリフレッシュトークン（type="refresh"）
- `decode_token(token) → dict`: JWT検証・デコード
- 設定: `JWT_SECRET_KEY`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`(30), `JWT_REFRESH_TOKEN_EXPIRE_DAYS`(7)

### encryption.py — 保存時暗号化
- `encrypt_json(data) → str | None`: JSON化可能なデータをFernet暗号化。`ENCRYPTION_KEY` 未設定時はNone
- `decrypt_json(encrypted) → Any | None`: 暗号化文字列を復号。鍵不一致やエラー時はNone
- `encrypt_text(text) → str | None` / `decrypt_text(encrypted) → str | None`: テキスト暗号化・復号
- `is_encryption_enabled() → bool`: 暗号化有効判定
- 方式: Fernet（AES-128-CBC + HMAC-SHA256）

### storage.py — DB永続化・テナント分離・監査ログ・削除
- `save_session(session, school_id=None, created_by=None)` / `load_session(session_id, school_id=None)` / `list_sessions(school_id=None)`: セッション永続化（school_id指定時はテナントフィルタ、暗号化カラム対応）
- `create_school()` / `get_school()` / `get_school_by_slug()`: 学校CRUD
- `create_user()` / `get_user()` / `get_user_by_email()`: ユーザーCRUD
- `seed_admin_user()`: 環境変数からデフォルト学校+管理者を冪等作成（CLI: `python -m storage seed-admin`）
- `export_csv()`: 採点結果をCSV文字列にエクスポート
- `export_csv_file()`: CSVファイル出力（BOM付きUTF-8でExcel対応）
- `log_audit_event()`: 監査ログ記録（HMACチェーンで改ざん検知）
- `list_audit_logs()`: 監査ログ検索（school_id, action, resource_type/id でフィルタ）
- `verify_audit_chain()`: HMACチェーンの整合性検証
- `delete_session()`: セッション削除（テナント検証付き、監査ログ記録）
- `purge_expired_sessions()`: 保存期間超過セッションの一括削除（学校別retention_days）
- `export_school_data()`: 学校全データエクスポート（解約・データポータビリティ用）
- `delete_school_data()`: 学校全データ完全削除（解約時）

### api/deps.py — FastAPI認証依存関係
- `CurrentUser`: JWT由来の認証済みユーザー情報（user_id, school_id, role）
- `get_current_user()`: 認証必須（401を返す）
- `get_optional_user()`: 認証オプショナル（ヘッダーなし→None、後方互換）

### app.py — UI
- **認証**: `check_auth()` — JWT認証（email+password）優先、ユーザー未登録時は旧パスワード認証にフォールバック
- **ルーブリック管理**: `api_client.py` 経由で API の parse/render を呼ぶ
- **プロバイダ構築**: `build_provider()` — session_state の設定を `provider_factory.py` に委譲し、UI/API で同じフォールバック規則を使う
- PDFアップロード・生徒ごとのページ分割
- ルーブリックビルダー・ローダー（YAML）
- インタラクティブ採点ワークフロー（4タブ構成）
- セッション管理・永続化・CSVエクスポート（API 経由）
- API利用に関するプライバシー同意
- **UI装飾ヘルパー**: `status_badge_html()`, `confidence_badge_html()`, `review_needed_badge_html()`, `progress_ring_html()`（SVG円形進捗リング）

### api/app.py — FastAPI v0.3.0
- **認証エンドポイント**:
  - `POST /api/v1/auth/login`: email+password → access_token + refresh_token
  - `POST /api/v1/auth/refresh`: refresh_token → 新access_token
  - `GET /api/v1/auth/me`: 認証済みユーザー情報
- **ルーブリック**: `/api/v1/rubrics/parse`, `/render`, `/refine`
- **セッション**: `/api/v1/sessions` (CRUD + DELETE) + `/exports/csv` — 全て `get_optional_user` でテナント分離
- **実行**: `/api/v1/runs/ocr`, `/api/v1/runs/horizontal-grading` — テナント検証付き
- **監査ログ**: `GET /api/v1/audit-logs`（検索）, `GET /api/v1/audit-logs/verify`（チェーン検証）
- **管理者**: `POST /api/v1/admin/purge-expired`（期限切れパージ）, `GET /api/v1/admin/schools/{id}/export`（全データエクスポート）, `DELETE /api/v1/admin/schools/{id}`（全データ削除）
- `GET /healthz`: ヘルスチェック
- **設計**: 全ルートが `get_optional_user` を使用（認証ヘッダーなしでも動作、移行期の後方互換）。全操作に監査ログ記録

### api_client.py — API クライアント
- `GRADING_API_BASE_URL` が設定されていれば外部APIへ HTTP 接続（認証トークン自動付与）
- 未設定時はローカル FastAPI app を直接呼ぶため、開発中も追加起動なしで API 境界を維持できる
- **認証**: `set_auth_token()` / `login()` / `refresh_access_token()` / `get_me()`
- **データ**: `load_rubric_from_yaml()` / `rubric_to_yaml()` / `list_sessions()` / `load_session()` / `save_session()` / `export_csv()`
- **実行**: `run_ocr()` / `run_horizontal_grading()` / `refine_rubric()`
- `_request()` にて `_auth_token` セット済みなら `Authorization: Bearer` ヘッダー自動付与

## 起動方法
```bash
python3 -m streamlit run app.py
```

## 環境変数
- `DATABASE_URL` — DB接続文字列（デフォルト: SQLite `data/grading.db`、本番: PostgreSQL）
- `JWT_SECRET_KEY` — JWT署名キー（必須、本番では強力なランダム文字列）
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` — アクセストークン有効期限（デフォルト: 30）
- `JWT_REFRESH_TOKEN_EXPIRE_DAYS` — リフレッシュトークン有効期限（デフォルト: 7）
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — 初期管理者（`python -m storage seed-admin` で使用）
- `GOOGLE_API_KEY` — Google Gemini APIキー（推奨）
- `ANTHROPIC_API_KEY` — Anthropic APIキー（オプション）
- `ENCRYPTION_KEY` — Fernet暗号化鍵（未設定時は暗号化無効）。生成: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `GRADING_API_BASE_URL` — 外部APIサーバーURL（未設定時はローカル直接呼び出し）

---

## 改善プラン進捗（2026-03-04〜）

教員・情報科学・アプリ開発の3視点から分析し、精度・操作性・堅牢性を改善中。
詳細プラン: `~/.claude/plans/radiant-strolling-torvalds.md`

### Phase 1: 即時対応 ✅ 全完了
1. ✅ Claude API temperature=0.2 設定（OCRは0.1）— 再現性確保
2. ✅ JSON パースのリトライ機構 — `_api_call_with_retry()` で全8 APIコールをリトライ対応、`_extract_json()` にJSON修復ロジック追加
3. ✅ PDF ページ数バリデーション — 割り切れない場合に警告
4. ✅ 小問配点の整合性チェック — リアルタイム表示 + 読込時にブロック
5. ✅ AI スコアの上限クランプ — 3つのパース関数で `[0, max_points]` に制限
6. ✅ ステータス凡例の追加 — レビュータブに折りたたみ式テーブル
7. ✅ AI コメントの表示改善 — `st.caption` → `st.info` で複数行表示

### Phase 2: 短期改善 ✅ 全完了
1. ✅ OCR 一括確認ボタン — 未確認の全学生を一括で「確認済み」にするボタン追加
2. ✅ ステータスワークフロー簡素化 — `pending → ai_scored → confirmed` の3段階に統合（旧reviewed互換維持）
3. ✅ 再採点時の確認ダイアログ — チェックボックスで上書き確認、未チェック時はボタン無効化
4. ✅ OCR プロンプトへの文脈情報追加 — 設問タイプ（短答/記述/選択）・期待回答形式のヒントを付与
5. ✅ 答案画像とOCR テキストの並列表示 — `st.columns([1,1])` で左:画像、右:OCRテキスト
6. ✅ API タイムアウト設定 — Anthropic: `timeout=120.0`, Gemini: `ThreadPoolExecutor` + 120秒タイムアウト
7. ✅ バッチサイズの推奨値表示 — `recommend_batch_size()` でルーブリック内容から自動算出・デフォルト値に反映

### Phase 3: 将来的改善 ✅ 全完了
1. ✅ Git初期化 — mainブランチで管理開始、.gitignore整備
2. ✅ レート制限 — `RateLimiter`クラス（スライディングウィンドウ方式）、Gemini 14RPM / Anthropic 50RPM
3. ✅ AIレスポンスのスキーマ検証 — `_validate_schema()` + スキーマ定数、4つのparse関数で統合
4. ✅ 適応的thinking budget — `_thinking_budget_for_question()` で記述問題は2倍のthinking token
5. ✅ テストスイート — pytest 50テスト（test_models / test_scoring_engine / test_storage）
6. ✅ バッチ間キャリブレーション — `analyze_batch_calibration()` + レビュータブに警告UI
7. ✅ st.rerun()削減 — 14箇所→4箇所に削減（on_click/on_changeコールバック化）

### Phase 4: UI改善（プロフェッショナル品質化） ✅ 全完了
対象ユーザー: 手作りソフトウェアに不慣れな文系教員。技術用語を避け、直感的に操作できるUIを目指す。

1. ✅ 技術用語に平易な併記を追加（OCR→文字読み取り、バッチサイズ→1回あたりの処理人数 等）
2. ✅ confidence日本語化（high→高、medium→中、low→低）+ ステータス英語表示修正
3. ✅ グローバルCSS注入（Noto Sans JP、教育機関向けブルー配色、角丸UI、カードメトリクス）
4. ✅ 前提未達時のUI非表示 + 「次のステップ→」誘導メッセージ（if/elif/else分岐で制御。st.stop()はタブ内で使わないこと）
5. ✅ ステータスバッジ（絵文字→HTMLバッジ）+ ウェルカム画面（4ステップ案内）
6. ✅ タブ2内ステッパーUI（HTML/CSSベースの4工程表示）
7. ✅ タブ3: 一括確定ボタン + 要確認一括クリアボタン
8. ✅ タブ3: 一覧テーブルモード（data_editorで全学生×全設問のスコア一括編集）
9. ✅ 進捗リング（SVGベース円形プログレス — サイドバーに配置）
10. ✅ サイドバーのブランド化（グラデーション背景、ブランドヘッダー、免責装飾）
11. ✅ 小問の一括テキスト入力モード（タブ区切りテキストの貼り付け対応）
12. ✅ スコア変更時の即時自動保存（on_changeコールバック + 最終保存時刻表示）
13. ✅ AIスコア保持と「AIスコアに戻す」（QuestionScore.ai_scoreフィールド追加）

### バグ修正 (Phase 4 後)
- ✅ CSSグローバルfont-family指定がMaterial Symbolsアイコンを上書き → `:not(.material-symbols-rounded)` で除外
- ✅ `already_graded` が空リスト `[]` → `bool()` でラップ（TypeError修正）
- ✅ Tab 2内の `st.stop()` がTab 3/4レンダリングを阻害 → `if/elif/else` に変更
- ✅ `unsafe_allow_html` HTML注入を `st.container()` で隔離
- ✅ file_uploader に明示的 `key="pdf_uploader"` 追加（タブ切替防止）

### 注意事項（CSS）
- グローバル `font-family` 指定時は `.material-symbols-rounded` を除外すること（GitHub Issue #10138）
- `unsafe_allow_html=True` のHTML注入は `st.container()` で隔離し、後続ウィジェットへの干渉を防ぐ

### Phase 5: 採点精度向上（2026-03-04）
東大模試問題の採点結果（`results_d38a5085.csv`）を評価した結果、以下の課題を特定:
- 中〜上位帯でやや甘めの採点傾向
- confidence が全件 "high"、needs_review フラグがゼロ（人間レビューが形骸化）
- 使用モデル gemini-2.5-flash は2世代前

#### 5-1: Gemini 3.1 Pro Preview 追加 ✅
- `GeminiProvider.MODELS` に `gemini-3.1-pro-preview` を追加、新デフォルトに設定
- 2.5 Flash / 2.5 Pro は選択肢として残存
- `app.py` の `build_provider()` フォールバックも更新

#### 5-2: ダブルチェック方式（検証パス） ✅
記述式問題の採点後にAIが自動で検証する2パス方式を導入。
- `VERIFICATION_SYSTEM_PROMPT`: 検証者ロール（採点者とは別視点）
- `build_verification_prompt()`: 初回スコア+コメント+ルーブリックを提示して検証依頼
- `parse_verification_result()`: 検証結果パーサー
- `verify_question_scores()`: バッチ分割で検証実行、スコア差異があれば needs_review=True
- 3プロバイダー全て（Gemini/Anthropic/Demo）に `verify_question_batch()` メソッド追加
- `run_horizontal_grading()` に `enable_verification` パラメータ追加
- UI: サイドバーに「ダブルチェック方式（記述式）」チェックボックス（デフォルトON）
- 採点結果に「✓検証済」バッジ表示（コメント内の「【検証結果】」で判定）

#### 5-3: 確信度・要確認フラグ改善 ✅
- `HORIZONTAL_GRADING_SYSTEM_PROMPT` に confidence/needs_review の明確な基準を追加
- 後処理ルール: 記述式満点→needs_review=True、部分点でhigh→mediumに補正

#### テスト: 58テスト全パス（既存54 + 検証系4テスト）

### Phase 1B: 認証・マルチテナント・セキュリティ基盤 ✅ 全完了（2026-03-13）

#### 認証・テナント基盤
1. ✅ 依存追加（PyJWT, bcrypt, psycopg2-binary, cryptography）+ Docker Compose（PostgreSQL 16）
2. ✅ DBスキーマ拡張 — `schools`, `users`, `audit_logs` テーブル追加、`scoring_sessions` に暗号化カラム追加
3. ✅ ドメインモデル追加 — `School`（retention_days付き）, `User` データクラス
4. ✅ 認証モジュール `auth.py` — bcryptハッシュ + JWT（access/refresh）
5. ✅ ストレージ層拡張 — School/User CRUD、テナント分離フィルタ
6. ✅ FastAPI認証 — `api/deps.py`（get_optional_user）、3認証エンドポイント、全ルート保護
7. ✅ Streamlit統合 — `check_auth()` でJWT認証優先、レガシーパスワードにフォールバック

#### セキュリティ・データ保護
8. ✅ 監査ログ — `audit_logs` テーブル + HMACチェーンによる改ざん検知、全操作に記録
9. ✅ 保存時暗号化 — Fernet（AES-128-CBC + HMAC-SHA256）、暗号化カラムと平文カラムの併存
10. ✅ 保存期間・削除設計 — 学校別 retention_days、自動パージ、手動削除、解約時全データエクスポート・完全削除
11. ✅ プライバシーポリシードラフト — `docs/privacy-policy-draft.md`（法務レビュー前）

#### テスト: 157テスト全パス

### テスト実行
```bash
python3 -m pytest tests/ -v
```

## 商用化・実用プロダクト化の方針（2026-03-12）

詳細は `productization-roadmap.md` を参照。

要点:

- 現状は「採点精度を改善し続けられる強いプロトタイプ」であり、次の主戦場は精度微調整より信頼基盤
- 目標は「AIが自動で採点すること」ではなく、「教員が根拠付きで、安全に、短時間で採点を確定できること」
- 最優先課題は、認証、学校単位のデータ分離、保存基盤、監査ログ、削除設計
- 現在の Streamlit 一体型構成は pilot には適しているが、商用では UI / API / ワーカー / DB / オブジェクト保存へ分離した方がよい
- evaluation/ は中核資産であり、今後は OCR 評価、CI 回帰チェック、モデル比較の基盤として強化する
- 課金や拡販より先に、学校が安心して導入できる運用性と説明可能性を整える

今後のエージェントAIは、商用化や広域導入を前提にした提案をする場合、
必ず `productization-roadmap.md` を確認してから設計判断を行うこと。

### 直近の対応状況（2026-03-13）
- ✅ Phase 1B 全完了（認証・テナント + セキュリティ・データ保護）— 157テスト全パス
- 認証基盤: JWT（bcrypt + PyJWT）、マルチテナント、FastAPI認証、Streamlit統合
- 監査ログ: `audit_logs` テーブル + HMACチェーン改ざん検知、全API操作に記録
- 保存時暗号化: `encryption.py`（Fernet）、`ENCRYPTION_KEY` 環境変数、暗号化カラムと平文カラム併存
- 削除設計: `delete_session`, `purge_expired_sessions`, `export_school_data`, `delete_school_data`
- 保存期間: `School.retention_days`（デフォルト365日）、学校別設定可能
- プライバシーポリシー: `docs/privacy-policy-draft.md`（法務レビュー前）
- 管理者API: パージ、学校データエクスポート・完全削除

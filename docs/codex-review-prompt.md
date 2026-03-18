# Codex レビュープロンプト 第4回（2026-03-14）

以下をそのまま Codex に渡してください。

---

## タスク

このリポジトリ（grading-assistant）の **包括的なコードレビュー（第4回）** を行ってください。

### レビュー履歴
- **第1回**（2026-03-13）: 認証の穴、データ保護の実効化、整合性・堅牢性の3ステップで修正完了
- **第2回**（2026-03-14）: 管理者API越権、暗号化ON時の退行、MFAカラム型、バックアップコード強度、監査ログ削除方針、refresh token失効の6件を修正完了
- **第3回**（2026-03-14）: HMACチェーンv2（PII除外）、監査ログsuperadmin専用化、provider fail-closed化、起動時設定検証（config.py）、復号失敗の明示化の5件を修正完了

これまでの3回のレビューで Critical / Warning 級の指摘はすべて修正済みです。
今回は **第3回の修正が確実に機能しているかの検証** と、**3回のレビューを経てなお残る設計・実装上の課題の発見** を行ってください。

## プロジェクト概要

教員向けの国語記述式答案 AI 採点支援ツール。
- Python / Streamlit（UI）+ FastAPI（API）+ SQLAlchemy Core + Alembic（DB）
- Gemini API / Anthropic API で答案画像の OCR と仮採点を行い、教員がレビュー・確定する
- JWT 認証（access + refresh + MFA/TOTP）、マルチテナント（school_id）、Fernet 暗号化、HMAC チェーン監査ログ
- 274 テスト（pytest）、約 13,700 行

## 読むべきファイル

### 必読（コアロジック）
- `CLAUDE.md` — プロジェクト方針
- `.handoff.md` — **第1回〜第3回の修正記録**（何を直したかの詳細はここ）
- `productization-roadmap.md` — 商用化ロードマップ（設計判断の背景）
- `config.py` — **第3回で新設**: 起動時設定検証（`validate_secrets()`）
- `storage.py` — DB 永続化、テナント分離、監査ログ（HMACv2）、暗号化、削除
- `auth.py` — 認証（bcrypt, JWT, TOTP, バックアップコード）
- `encryption.py` — Fernet 暗号化
- `provider_factory.py` — **第3回で修正**: プロバイダ生成（fail-closed + タプル戻り値）
- `api/app.py` — 全 FastAPI エンドポイント（22 本 + レート制限 + DecryptionErrorハンドラ）
- `api/deps.py` — 認証依存関係（get_current_user / get_optional_user）
- `api_client.py` — Streamlit → API クライアント（contextvars トークン分離）
- `scoring_engine.py` — 採点エンジン（マルチプロバイダ、横断採点、検証パス）
- `models.py` — データモデル
- `db.py` — テーブル定義（hash_version カラム追加済み）

### 参考（テスト）
- `tests/` 配下全体 — 特に `test_integrity.py`, `test_data_protection.py`, `test_mfa.py`, `test_codex_review_fixes.py`, `test_provider_factory.py`

## レビュー観点

以下の観点それぞれについて、具体的なファイル名・行番号・コード片を示しながら指摘してください。

### 1. 第3回修正の検証（最優先）

第3回で修正した以下の各項目が正しく機能しているか検証してください:

- **HMACチェーンv2**: `_compute_audit_hash()` の署名対象がPII（user_id, details, ip_address）を除外しているか。`hash_version` カラムの導入とマイグレーション（`h8c9d0e1f234`）は安全か。匿名化後もチェーンが壊れないことが保証されているか
- **監査ログ権限**: `GET /api/v1/audit-logs` が admin 専用、`/api/v1/audit-logs/verify` が superadmin 専用に正しく制限されているか。teacher / 一般ユーザーのアクセスが確実にブロックされているか
- **provider fail-closed**: `build_provider()` が gemini/anthropic 指定時にAPIキー未設定で `ValueError` を送出するか。DemoProvider へのサイレントフォールバックが完全に排除されているか。タプル戻り値 `(provider, resolved_provider_name)` の展開が呼び出し元すべてで正しいか
- **起動時設定検証**: `config.py` の `validate_secrets()` が本番環境で `JWT_SECRET_KEY` / `ENCRYPTION_KEY` 未設定時に起動を拒否するか。開発環境でのフォールバックは適切か。`AUDIT_HMAC_KEY` の分離は機能しているか
- **復号失敗の明示化**: `_decrypt_user_fields()` / `get_api_key()` が暗号化有効時に復号失敗で `DecryptionError` を送出するか。`api/app.py` の例外ハンドラが 503 を返すか

### 2. セキュリティ深掘り

3回のレビューを経ても見落とされた可能性のある問題を探してください:

- **認証・認可**: 全22エンドポイントの認証要件を列挙し、不整合がないか確認。特にsuperadmin / admin / teacher の3ロール間の権限分離
- **IDOR（Insecure Direct Object Reference）**: session_id, school_id, user_id 等のリソースアクセスで、他テナントのデータに到達できるパスがないか
- **トークンライフサイクル**: access token / refresh token / MFA pending token それぞれの発行→使用→失効の全フローを追跡
- **暗号化の境界**: 平文がメモリ/ログ/レスポンスに露出するポイントの洗い出し。DecryptionError 発生時に平文やスタックトレースが漏洩しないか
- **依存ライブラリ**: requirements.txt のバージョン固定状況、既知の脆弱性
- **config.py の堅牢性**: 環境変数の解釈ミス、APP_ENV のバリデーション不足、フォールバック挙動に抜け穴がないか

### 3. データ整合性

- **テナント分離の完全性**: school_id フィルタが必要な全クエリを列挙し、漏れがないか。superadmin のグローバルアクセスとの整合性
- **DB スキーマ vs コード**: db.py のテーブル定義と storage.py のクエリが一致しているか。特に `hash_version` カラムの使用箇所
- **マイグレーション安全性**: 全3本のAlembicマイグレーション（第1回〜第3回）のチェーン整合性とロールバック可能性
- **HMACチェーン整合性**: v1→v2 の移行マイグレーションで既存データが正しく再署名されているか

### 4. 設計・アーキテクチャ

- **storage.py の肥大化**: 現在の行数と責務を評価し、分割が必要なら具体的な分割案を提示
- **config.py の設計**: 新設された設定検証モジュールの責務範囲は適切か。将来的な設定項目追加に耐えうるか
- **エラーハンドリング一貫性**: HTTPException / DecryptionError / ValueError / ConfigurationError の使い分けに不統一がないか
- **API 設計**: RESTful 原則との乖離、レスポンス形式の一貫性

### 5. 堅牢性・運用

- **並行性**: マルチワーカーでの HMAC チェーン整合性（v2移行後も含む）、レート制限カウンタの正確性
- **障害時の振る舞い**: DB 接続断、Gemini API タイムアウト、暗号化鍵の不一致、復号失敗が起きた場合の挙動。503 レスポンスの適切性
- **可観測性**: 本番運用に必要なログ/メトリクスが不足している箇所。config.py のバリデーション結果のログ出力

### 6. コード品質

- **DRY 違反**: 重複コードや類似パターンの統合余地
- **デッドコード**: 未使用の関数、import、変数
- **型安全性**: 型アノテーションの欠如で実行時エラーになりうる箇所。特にタプル戻り値の展開漏れ
- **テストの網羅性**: 274テストでカバーされていない重要なパス

## 出力フォーマット

以下の形式で出力してください：

```
## 第3回修正の検証結果

各項目について ✅ OK / ⚠️ 要改善 / ❌ 未修正 で評価し、要改善・未修正の場合は具体的な問題を記述。

## 新規指摘一覧

### [Critical / Warning / Info] 指摘タイトル
- **ファイル**: `path/to/file.py:123`
- **問題**: 何が問題か（具体的に）
- **影響**: どのような被害・障害が起きうるか
- **修正案**: 具体的な修正方法（コード片があれば添える）

（繰り返し）

## 総評
- 3回の修正を経たセキュリティ態勢の全体評価
- 商用化に向けて残っている技術的リスク（優先度順）
- Phase2 で対応予定の項目（.handoff.md 記載）の優先順位に対する意見
```

## 注意事項

- 前回までの修正内容は `.handoff.md` に詳細な記録がある。必ず読むこと
- Phase2 で対応予定の項目（Redis移行、refresh token family、token_invalidated_at セット処理等）は `.handoff.md` 末尾に記載。これらは「既知の制限」として扱い、Critical 指摘にしないこと。ただし、Phase2 の優先順位に対する意見は歓迎
- 商用化の方針は `productization-roadmap.md` に記載。設計判断はこの方針に照らして評価すること
- テストは `python3 -m pytest tests/ -q` で実行可能（274 passed）
- コードを変更しないこと。レビューのみ

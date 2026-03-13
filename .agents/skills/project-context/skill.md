---
description: 採点補助アプリ (grading-assistant) の開発に関するタスク全般。機能追加、バグ修正、UI改善、採点ロジック調整など。
---

# Grading Assistant — プロジェクトコンテキスト

## 概要
国語の試験採点を支援するStreamlitアプリ。手書き答案のPDFをAI（Gemini/Codex）でOCR・仮採点し、教師の採点作業を補助する。
AI採点はあくまで参考値であり、最終判断は教師が行う設計思想。

## 技術スタック
- 言語: Python 3
- UI: Streamlit (>=1.33.0)
- AI: Google Gemini API (google-genai, 推奨) / Anthropic Codex API (anthropic, オプション)
- PDF処理: PyMuPDF (>=1.24.0)
- 画像処理: Pillow (>=10.0.0)
- 設定: PyYAML (>=6.0) — ルーブリック定義
- 環境変数: python-dotenv
- テスト: pytest (>=8.0.0)
- バージョン管理: Git（mainブランチ）

## ディレクトリ構成
```
grading-assistant/
├── app.py                # メインStreamlitアプリ
├── api_client.py         # Streamlit から API を呼ぶためのクライアント
├── rubric_io.py          # ルーブリック変換・YAML入出力の共通モジュール
├── models.py             # データモデル（Rubric, Question, StudentResult等）
├── scoring_engine.py     # 採点エンジン（マルチAPI対応）
├── pdf_processor.py      # PDF→画像変換・処理
├── storage.py            # セッション永続化（JSON）・CSVエクスポート
├── api/
│   └── app.py            # FastAPIによるAPIレイヤー（フェーズ1の初期スライス）
├── requirements.txt      # 依存パッケージ
├── .env.example          # 環境変数テンプレート（APIキー）
├── docs/adr/             # アーキテクチャ判断記録
├── rubrics/              # 採点ルーブリック定義（YAML）
│   ├── sample_rubric.yaml
│   ├── test_rubric.yaml
│   └── todai_rubric.yaml
├── tests/                # テストスイート（pytest）
│   ├── conftest.py       # 共有フィクスチャ
│   ├── test_models.py
│   ├── test_scoring_engine.py
│   └── test_storage.py
├── data/                 # 保存済み採点セッション（JSON）
├── output/               # エクスポート結果
├── test_data/            # サンプルPDF
├── run.sh / run.bat      # 起動スクリプト
└── setup.sh / setup.bat  # セットアップスクリプト
```

## 主要モジュール

### models.py — データモデル
- `Rubric`: 試験メタデータと問題定義（title, total_points, pages_per_student, questions, notes）
- `Question` / `SubQuestion`: 個別問題（配点・解答タイプ: short_answer / descriptive / selection）
- `StudentResult`: 生徒別採点結果（is_reference フラグで教員採点を参考例としてAIに提供可能）
- `QuestionScore`: 問題別スコア（信頼度・要レビューフラグ・ai_score バックアップ付き）
- `ScoringSession`: 複数生徒を含む採点セッション全体
  - `from_dict()` / `to_dict()`: JSON永続化用シリアライズ
  - `get_reference_students()`: 参考例マーク済み学生の取得
  - `get_ocr_for_student()` / `get_all_answers_for_question()`: OCRデータアクセス
  - `ocr_complete()`: 全学生OCR完了判定
  - `summary()`: 統計サマリー（採点済み数・平均点・要確認数）
- `StudentOcr` / `OcrAnswer`: 手書きOCR結果（問題別読み取りテキスト・信頼度・手動修正フラグ）

### scoring_engine.py — 採点エンジン
- **プロバイダ抽象化** (`ScoringProvider` ABC → `GeminiProvider` / `AnthropicProvider` / `DemoProvider`)
  - GeminiProvider: モデル選択肢 `gemini-3.1-pro-preview`（デフォルト）/ `gemini-2.5-flash` / `gemini-2.5-pro`、120秒タイムアウト（ThreadPoolExecutor）
  - AnthropicProvider: モデル選択肢 `Codex-sonnet-4-20250514`（デフォルト）/ `Codex-haiku-4-20250414`、120秒タイムアウト
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

### storage.py — セッション永続化
- `save_session()` / `load_session()` / `list_sessions()`: JSON形式でのセッション永続化（`data/` ディレクトリ）
- `export_csv()`: 採点結果をCSV文字列にエクスポート（設問ごとの得点・配点・読取・コメント・確信度・要確認を列に展開）
- `export_csv_file()`: CSVファイル出力（BOM付きUTF-8でExcel対応、`output/` ディレクトリ）

### app.py — UI
- **認証**: `check_password()` — 共通パスワード認証（`st.secrets["password"]`、未設定時はスルー）
- **ルーブリック管理**: `api_client.py` 経由で API の parse/render を呼ぶ
- **プロバイダ構築**: `build_provider()` — session_state の設定からプロバイダを自動選択
- PDFアップロード・生徒ごとのページ分割
- ルーブリックビルダー・ローダー（YAML）
- インタラクティブ採点ワークフロー（4タブ構成）
- セッション管理・永続化・CSVエクスポート（API 経由）
- API利用に関するプライバシー同意
- **UI装飾ヘルパー**: `status_badge_html()`, `confidence_badge_html()`, `review_needed_badge_html()`, `progress_ring_html()`（SVG円形進捗リング）

### api/app.py — FastAPI 初期スライス
- `GET /healthz`: APIヘルスチェック
- `POST /api/v1/rubrics/parse`: YAML採点基準のパースと要約取得
- `POST /api/v1/rubrics/render`: Rubric相当dictを YAML に戻す
- `GET /api/v1/sessions`: 保存済みセッション一覧
- `POST /api/v1/sessions`: セッションの新規作成
- `GET /api/v1/sessions/{session_id}` / `PUT /api/v1/sessions/{session_id}`: セッション取得・保存
- `GET /api/v1/sessions/{session_id}/exports/csv`: CSVエクスポート文字列
- `POST /api/v1/runs/ocr`: PDF + rubric + provider設定を受け取り、OCRを実行してセッション更新
- `POST /api/v1/runs/horizontal-grading`: 保存済みセッションに対して横断採点を実行してセッション更新

### api_client.py — API クライアント
- `GRADING_API_BASE_URL` が設定されていれば外部APIへ HTTP 接続
- 未設定時はローカル FastAPI app を直接呼ぶため、開発中も追加起動なしで API 境界を維持できる
- `load_rubric_from_yaml()` / `rubric_to_yaml()` / `list_sessions()` / `load_session()` / `save_session()` / `export_csv()` を提供
- `run_ocr()` / `run_horizontal_grading()` で OCR・採点開始も API 経由化

## 起動方法
```bash
python3 -m streamlit run app.py
```

## 環境変数
- `GOOGLE_API_KEY` — Google Gemini APIキー（推奨）
- `ANTHROPIC_API_KEY` — Anthropic APIキー（オプション、$5最低チャージ必要）

---

## 改善プラン進捗（2026-03-04〜）

教員・情報科学・アプリ開発の3視点から分析し、精度・操作性・堅牢性を改善中。
詳細プラン: `~/.Codex/plans/radiant-strolling-torvalds.md`

### Phase 1: 即時対応 ✅ 全完了
1. ✅ Codex API temperature=0.2 設定（OCRは0.1）— 再現性確保
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
- ✅ 外部AI送信前の氏名マスキングを実装。Gemini / Codex に送る画像は、先頭ページ上部をマスクしたコピーへ自動差し替え
- ✅ OCRプロンプトを調整し、マスキング時は `student_name` を空文字で返すよう明示
- ✅ サイドバーに氏名欄位置・マスク幅/高さの設定を追加（既定ON）
- ✅ API First 移行のADRを追加。FastAPI / PostgreSQL / RLS / Alembic 方針を文書化
- ✅ `rubric_io.py` と `api/app.py` を追加し、ルーブリック変換とセッション永続化の初期APIを実装
- ✅ `api_client.py` を追加し、Streamlit のルーブリック変換・セッション保存/読込・CSV出力を API 経由へ切り替え
- ✅ OCR開始・まとめ採点・お手本再採点も API run エンドポイント経由へ切り替え
- ✅ テストスイートを拡張し、`python3 -m pytest tests -q` で 79 tests passed

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
- AI: Google Gemini API (google-genai, 推奨) / Anthropic Claude API (anthropic, オプション)
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
├── models.py             # データモデル（Rubric, Question, StudentResult等）
├── scoring_engine.py     # 採点エンジン（マルチAPI対応）
├── pdf_processor.py      # PDF→画像変換・処理
├── storage.py            # セッション永続化（JSON）・CSVエクスポート
├── requirements.txt      # 依存パッケージ
├── .env.example          # 環境変数テンプレート（APIキー）
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
- `Rubric`: 試験メタデータと問題定義
- `Question` / `SubQuestion`: 個別問題（配点・解答タイプ）
- `StudentResult`: 生徒別採点結果
- `QuestionScore`: 問題別スコア（信頼度・要レビューフラグ付き）
- `ScoringSession`: 複数生徒を含む採点セッション全体
- `StudentOcr`: 手書きOCR結果

### scoring_engine.py — 採点エンジン
- マルチプロバイダ抽象化（Gemini / Claude / Demoモード）
- キャリブレーション例付き採点プロンプト
- 手書きOCR（文字認識）
- バッチ採点（バッチサイズ設定可能）
- 横断採点モード（複数生徒を一貫した基準で採点）
- 信頼度トラッキング・要レビューフラグ
- RateLimiter: スライディングウィンドウ方式（Gemini 14RPM, Anthropic 50RPM）
- _validate_schema(): AIレスポンスの構造検証（4つのparse関数で使用）
- _thinking_budget_for_question(): 問題タイプに応じたGemini thinking token調整
- analyze_batch_calibration(): バッチ間スコア分布の偏り検出

### app.py — UI
- PDFアップロード・生徒ごとのページ分割
- ルーブリックビルダー・ローダー（YAML）
- インタラクティブ採点ワークフロー
- セッション管理・永続化
- CSVエクスポート
- API利用に関するプライバシー同意

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

### Phase 4: UI改善（プロフェッショナル品質化） — 進行中
対象ユーザー: 手作りソフトウェアに不慣れな文系教員。技術用語を避け、直感的に操作できるUIを目指す。

**完了済み:**
1. ✅ 技術用語に平易な併記を追加（OCR→文字読み取り、バッチサイズ→1回あたりの処理人数 等）
2. ✅ confidence日本語化（high→高、medium→中、low→低）+ ステータス英語表示修正
3. ✅ グローバルCSS注入（Noto Sans JP、教育機関向けブルー配色、角丸UI、カードメトリクス）
4. ✅ st.stop()で前提未達時のUI非表示 + 「次のステップ→」誘導メッセージ
5. ✅ ステータスバッジ（絵文字→HTMLバッジ）+ ウェルカム画面（4ステップ案内）
6. ✅ タブ2内ステッパーUI（HTML/CSSベースの4工程表示）
7. ✅ タブ3: 一括確定ボタン + 要確認一括クリアボタン
8. ✅ タブ3: 一覧テーブルモード（data_editorで全学生×全設問のスコア一括編集）

**未着手:**
- 進捗リング（SVGベース円形プログレス）
- サイドバーのブランド化（ダーク背景対応テキスト色）
- 小問の一括テキスト入力モード
- スコア変更時の即時自動保存
- AIスコア保持と「AIスコアに戻す」

### テスト実行
```bash
python3 -m pytest tests/ -v
```

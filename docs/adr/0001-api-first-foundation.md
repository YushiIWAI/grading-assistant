# ADR 0001: API First で Streamlit からサービス境界を切り出す

- Status: Accepted
- Date: 2026-03-13

## Context

現状の grading-assistant は Streamlit UI、採点ロジック、ローカルJSON保存が一体化している。
プロトタイプとしては十分に機能している一方で、学校単位の導入を前提にすると次の問題がある。

- フロントエンドのセッション状態が事実上のアプリケーション状態になっている
- 認証・監査・ジョブ管理を UI プロセスへ載せ替えにくい
- 将来的に Web フロント、管理画面、バッチワーカーを分離しにくい
- DB / オブジェクトストレージ / 非同期ワーカーへの移行経路が曖昧

ロードマップでも、フェーズ1冒頭で API 層を切り出すことを最優先としている。

## Decision

grading-assistant は段階的に API First 構成へ移行する。

今回の決定内容:

1. API フレームワークは FastAPI を採用する
2. 永続層の最終ターゲットは PostgreSQL + Alembic とする
3. テナント分離は `school_id` を軸にした PostgreSQL Row Level Security を第一候補とする
4. 答案PDF・画像・CSVエクスポートはオブジェクトストレージへ保存する前提で設計する
5. ただし今回の実装では、移行ブリッジとして既存のローカルJSONストレージを API 配下から利用する

## Why FastAPI

- Python 中心の既存コードベースと親和性が高い
- 型ヒントから request / response を定義しやすい
- Streamlit から HTTP 経由で呼び出す薄い移行がしやすい
- バックグラウンドジョブ化や認証依存関係の導入が比較的容易

## Why PostgreSQL + RLS

- 学校単位のデータ分離、監査ログ、採点run再現性の保存に向いている
- JSONB を使って移行初期の柔軟なスキーマを許容しつつ、後から正規化しやすい
- RLS により API / worker / 管理画面で共通のテナント境界を維持しやすい

## Migration Strategy

移行は 3 段階で進める。

### Step 1: API 境界の確立

- ルーブリック変換とセッション永続化を FastAPI へ切り出す
- Streamlit 直下の共有ロジックを共通モジュールへ移す
- 既存の JSON 保存を API の内側で使い、UI は将来的に API 呼び出しへ差し替える

### Step 2: データストア移行

- セッション、採点run、OCR結果、監査イベントを PostgreSQL へ移す
- Alembic でマイグレーション管理を開始する
- JSON ファイルは移行ツール経由でDBへ取り込む

### Step 3: 非同期化

- OCR / 採点 / 検証をジョブ化し、API は run 作成と状態参照だけを担う
- フロントはポーリングまたはサーバープッシュで進捗を見る

## Consequences

### Positive

- Streamlit からフロント差し替え可能な境界ができる
- 認証、監査、学校単位の権限制御を API 側へ集約しやすくなる
- バックグラウンドジョブや DB 移行の準備が進む

### Negative

- 当面は JSON 保存と API の二重構造になり、完全移行前のコード量が増える
- FastAPI / uvicorn / httpx など新しい依存が増える
- Streamlit がまだ API を使っていない間は、分離の恩恵が一部に留まる

## First Slice Implemented Now

- `rubric_io.py` を追加し、ルーブリック変換を Streamlit から分離
- `api/app.py` を追加し、以下を API 化:
  - `GET /healthz`
  - `POST /api/v1/rubrics/parse`
  - `POST /api/v1/rubrics/render`
  - `GET /api/v1/sessions`
  - `POST /api/v1/sessions`
  - `GET /api/v1/sessions/{session_id}`
  - `PUT /api/v1/sessions/{session_id}`
  - `GET /api/v1/sessions/{session_id}/exports/csv`

## Deferred

- 認証方式の確定（MFA / SSO / 招待フロー）
- PostgreSQL スキーマと `school_id` 境界の詳細定義
- オブジェクトストレージのベンダ選定
- OCR / 採点実行のジョブキュー化

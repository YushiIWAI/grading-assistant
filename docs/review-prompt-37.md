# コードレビュー指示（37回目セッション後）

以下のプロンプトをClaude Codeの新しいチャットにコピペして使ってください。

---

## プロンプト

```
/Users/yushiiwai/Desktop/grading-assistant/ のコードレビューをお願いします。

### 背景
このプロジェクトは国語科の記述式答案をAI（Gemini/Claude）で仮採点するStreamlitアプリです。
直近のセッション（37回目）で以下の大きな変更を入れました:

1. ルーブリック精緻化をパターンベースに再設計（個別回答→パターングルーピング）
2. typed入力時のthinking_budget最適化（is_typedフラグの伝搬）
3. IDマッチングの柔軟化（_resolve_student_id, _normalize_sid）
4. 生徒向けフィードバック生成（QuestionScore.feedback）
5. 表記・文法の減点オプション（GradingOptions）
6. CSVエクスポート（csv_exporter.py）
7. 検証パスでのfeedback補填

### レビュー観点

以下の観点でレビューしてください。各観点ごとにエージェントを並列で走らせて構いません。

#### 1. セキュリティ・脆弱性
- 新しい入力パス（CSV入力、GradingOptions）にインジェクションリスクはないか
- APIキーやユーザーデータの取り扱いに問題はないか
- _resolve_student_id のマッチングロジックに意図しない衝突（別の生徒にマッチ）のリスクはないか

#### 2. データ整合性
- QuestionScore.feedback の追加が既存のシリアライズ/デシリアライズ（JSON, YAML, DB）に影響しないか
- GradingOptions の追加が既存のRubric読み書き（rubric_io.py, API）で問題を起こさないか
- is_typed パラメータが全プロバイダー（Gemini, Anthropic, Demo）で一貫して伝搬しているか
- grading_options が全プロバイダーで一貫して伝搬しているか

#### 3. プロンプト品質
- HORIZONTAL_GRADING_SYSTEM_PROMPT のfeedback指示は適切か（生徒に不適切な内容が生成されるリスク）
- RUBRIC_REFINE_SYSTEM_PROMPT のパターンベース指示で、パターン数が多すぎ/少なすぎになるケースはないか
- VERIFICATION_SYSTEM_PROMPT のfeedback指示で、得点変更なしの場合にも不要なfeedbackが生成されないか
- _build_grading_options_prompt の出力が採点結果を歪めるリスクはないか

#### 4. エッジケース・堅牢性
- CSV入力で空行、重複ID、特殊文字（カンマ、改行含む回答）の処理は適切か
- _resolve_student_id で全く異なる生徒にマッチする可能性（例: "1-1" と "1-10"）
- GradingOptions の全フィールドがFalseの場合（何も減点しない）の動作
- feedback が空文字列の場合のUI表示・CSVエクスポート
- 30人を超える大規模クラス（50-60人）でのバッチ処理の安定性

#### 5. コード品質
- 変更量が多いため、デッドコードや未使用のインポートが残っていないか
- テストカバレッジ: csv_exporter.py のユニットテストが存在しない
- app.py の肥大化（2200行超）に対する分割提案があれば

### 参照すべきファイル
- `.handoff.md` — 変更履歴と技術的コンテキスト
- `CLAUDE.md` — プロジェクトルールと制約
- `.claude/skills/project-context/skill.md` — 技術リファレンス

### 出力形式
各観点ごとに:
- 🔴 Critical（即修正が必要）
- 🟡 Warning（改善推奨）
- 🟢 OK（問題なし）
で分類し、Criticalがあれば修正コードも提示してください。
```

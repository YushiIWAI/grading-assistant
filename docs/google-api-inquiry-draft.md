# Google Cloud Sales への問い合わせ（ドラフト）

## 問い合わせ先
- Google Cloud Sales: https://cloud.google.com/contact
- または Google AI for Developers のサポート

## 件名
教育機関向けSaaSにおけるGemini API利用に関する利用規約の確認

## 本文

お世話になっております。

教育機関（中学校・高等学校）向けの採点支援SaaSを開発しております。
本サービスにおけるGemini APIの利用が利用規約に適合するか確認させていただきたく、ご連絡いたしました。

### サービスの概要
- 教員がGoogle Classroomの回答データ（CSV）を取り込み、AIが記述式回答の仮採点と生徒向けフィードバックを生成するツールです
- エンドユーザーは教員のみであり、生徒がアプリケーションにアクセスすることはありません
- API送信時には生徒の氏名・ID等の個人識別情報を自動的に匿名化（仮番号に置換）しており、Gemini APIには個人を特定できないテキストデータのみが送信されます

### 確認したい事項

1. **18歳未満条項について**: Gemini API Additional Terms of Service に「Services that are directed towards or likely to be accessed by individuals under the age of 18」への使用禁止が規定されています。本サービスは教員向けツールであり、生徒（18歳未満）はアプリケーションに一切アクセスしません。また、送信データは匿名化済みです。この利用形態は当該条項に抵触しますでしょうか。

2. **有料API（Cloud Billing紐付け）での利用を前提としています。** DPA（Data Processing Addendum）が適用され、送信データがモデル学習に使用されないことを確認したいです。

3. **商用SaaSとしての提供**: 本サービスを学校向けに有料SaaSとして提供する場合、Gemini APIの利用規約上の制約（再販禁止等）はありますでしょうか。

4. **Vertex AI への移行**: 将来的にデータ処理のリージョン指定（日本国内）が必要になった場合、Vertex AI Gemini APIへの移行を検討しています。Vertex AI経由の場合、上記の確認事項に変更はありますでしょうか。

ご回答は書面（メール）でいただけますと、法務レビューの際の根拠資料として使用させていただきたく存じます。

何卒よろしくお願いいたします。

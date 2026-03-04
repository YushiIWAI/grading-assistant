"""テスト用の答案PDFと採点基準を生成するスクリプト"""

import fitz  # PyMuPDF
from pathlib import Path


# ============================================================
# テスト用の答案データ
# ============================================================

EXAM_TITLE = "国語テスト（テスト用）"

# 問題文（各ページ共通で表示）
QUESTIONS = {
    1: {
        "text": (
            "【問1】次の文の空欄に入る最も適切な語句を、ア～エから一つ選び、記号で答えなさい。（5点）\n"
            "「彼の説明は終始（　　）としており、聞く者を深く納得させた。」\n"
            "ア. 明瞭　　イ. 曖昧　　ウ. 冗長　　エ. 簡潔"
        ),
    },
    2: {
        "text": (
            "【問2】次の文章を読み、筆者の主張を60字以内で要約しなさい。（10点）\n\n"
            "　SNSの普及により、私たちは日々膨大な情報にさらされている。しかし、その中には\n"
            "真偽の定かでないものも少なくない。情報を鵜呑みにするのではなく、複数の情報源を\n"
            "比較し、批判的に検討する姿勢――すなわちメディアリテラシー――が、現代を生きる\n"
            "私たちにとって不可欠な力となっている。"
        ),
    },
    3: {
        "text": (
            "【問3】本文の主張を踏まえ、あなたが考える「情報との向き合い方」について、\n"
            "100字以内で自分の意見を述べなさい。（15点）"
        ),
    },
}

# 5名分の解答（さまざまなレベル）
STUDENTS = [
    {
        "name": "山田 太郎",
        "number": "1",
        "answers": {
            1: "ア",
            2: (
                "SNSの普及で真偽不明の情報が氾濫する現代では、"
                "複数の情報源を比較し批判的に検討するメディアリテラシーが不可欠である。"
            ),
            3: (
                "私は、情報を受け取る際にまず「本当だろうか」と疑う習慣が大切だと考える。"
                "例えば、SNSで話題のニュースを見たとき、すぐに拡散せず、"
                "公的機関の発表や複数の報道を確認するようにしている。"
                "筆者の言うメディアリテラシーとは、こうした日常の小さな実践の積み重ねだと思う。"
            ),
        },
        "expected_score": "28/30（優秀な答案）",
    },
    {
        "name": "佐藤 花子",
        "number": "2",
        "answers": {
            1: "ア",
            2: (
                "情報がたくさんある時代だから、"
                "正しい情報を選ぶ力が大事だということ。"
            ),
            3: (
                "本文にもあるように、情報を見極める力は大切だと思います。"
                "私もSNSをよく使うので、嘘の情報に気をつけたいです。"
                "ただ、どれが正しいかを判断するのは難しいので、"
                "信頼できる人に聞くのも一つの方法だと思います。"
            ),
        },
        "expected_score": "22/30（良好だが具体性にやや欠ける）",
    },
    {
        "name": "鈴木 一郎",
        "number": "3",
        "answers": {
            1: "エ",  # 誤答（簡潔≠明瞭）
            2: (
                "SNSには色々な情報があるので気をつけなければならない。"
            ),
            3: (
                "情報はたくさんあるので便利だけど、"
                "間違った情報もあるから注意しないといけないと思った。"
            ),
        },
        "expected_score": "12/30（部分点。要約が不十分、意見も浅い）",
    },
    {
        "name": "田中 美咲",
        "number": "4",
        "answers": {
            1: "ア",
            2: "情報が多い時代である。",
            3: (
                "私はスマホでよく動画を見ます。"
                "面白い動画がたくさんあって楽しいです。"
                "友達ともよく動画の話をします。"
            ),
        },
        "expected_score": "8/30（問1正解。問2は不十分。問3は本文と無関係）",
    },
    {
        "name": "高橋 健太",
        "number": "5",
        "answers": {
            1: "ウ",  # 誤答
            2: "",  # 白紙
            3: "わからない。",
        },
        "expected_score": "0/30（ほぼ白紙）",
    },
]


# ============================================================
# PDF生成
# ============================================================

def find_japanese_font() -> str | None:
    """macOSの日本語フォントファイルを探す"""
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Hiragino Sans W3.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def create_test_pdf(output_path: str):
    """テスト用答案PDFを生成する（5名分、1名1ページ）"""
    doc = fitz.open()
    font_path = find_japanese_font()

    for student in STUDENTS:
        page = doc.new_page(width=595, height=842)  # A4

        # フォント登録（insert_fontはxrefを返すので、名前は別途指定）
        if font_path:
            page.insert_font(fontfile=font_path, fontname="jp")
            fontname = "jp"
        else:
            fontname = "helv"  # フォールバック

        y = 40

        # --- ヘッダー ---
        page.insert_text(
            (40, y), EXAM_TITLE,
            fontname=fontname, fontsize=14,
        )
        y += 25
        page.insert_text(
            (40, y), f"出席番号: {student['number']}　　氏名: {student['name']}",
            fontname=fontname, fontsize=11,
        )
        y += 10
        page.draw_line((40, y), (555, y))  # 横線
        y += 20

        # --- 各問題と解答 ---
        for q_num in [1, 2, 3]:
            q = QUESTIONS[q_num]
            answer = student["answers"][q_num]

            # 問題文
            for line in q["text"].split("\n"):
                if not line.strip():
                    y += 8
                    continue
                # テキストボックスで折り返し表示
                rect = fitz.Rect(40, y, 555, y + 15)
                page.insert_textbox(
                    rect, line,
                    fontname=fontname, fontsize=9.5,
                    color=(0.2, 0.2, 0.2),
                )
                y += 15

            y += 8

            # 解答欄ラベル
            page.insert_text(
                (50, y), "【解答】",
                fontname=fontname, fontsize=10,
                color=(0, 0, 0.6),
            )
            y += 5

            # 解答枠
            if answer:
                # 解答を折り返して表示
                box_top = y
                answer_rect = fitz.Rect(55, y, 540, y + 100)
                rc = page.insert_textbox(
                    answer_rect, answer,
                    fontname=fontname, fontsize=11,
                    color=(0, 0, 0),
                )
                # 実際に使った高さを推定
                lines_used = max(1, len(answer) // 40 + 1)
                text_height = lines_used * 16
                box_bottom = box_top + max(text_height, 25)
            else:
                box_top = y
                box_bottom = y + 25
                page.insert_text(
                    (60, y + 15), "（未記入）",
                    fontname=fontname, fontsize=10,
                    color=(0.6, 0.6, 0.6),
                )

            # 解答枠の線
            page.draw_rect(fitz.Rect(50, box_top, 545, box_bottom + 5),
                           color=(0.7, 0.7, 0.7), width=0.5)
            y = box_bottom + 20

        # --- フッター ---
        page.draw_line((40, y), (555, y))
        y += 15
        page.insert_text(
            (40, y),
            f"※テスト用データ（想定得点: {student['expected_score']}）",
            fontname=fontname, fontsize=8,
            color=(0.5, 0.5, 0.5),
        )

    doc.save(output_path)
    doc.close()
    print(f"テスト用PDF を生成しました: {output_path}")
    print(f"  - {len(STUDENTS)}名分（{len(STUDENTS)}ページ）")


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    out_dir = Path(__file__).parent / "test_data"
    out_dir.mkdir(exist_ok=True)
    create_test_pdf(str(out_dir / "test_answers.pdf"))
    print(f"\n対応する採点基準: rubrics/test_rubric.yaml")

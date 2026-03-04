"""東大型 現代文模擬試験のテスト答案PDFを生成するスクリプト

出典（架空）: 石川哲郎「聴くことの倫理――対話における受動性の意味」
形式: 東大 第一問 準拠（2行論述×3 + 120字論述×1 + 漢字×3）

5名分のリアルな答案を生成する。得点レベルは優秀〜低得点まで幅広く設定。
"""

import fitz  # PyMuPDF
from pathlib import Path


# ============================================================
# 本文テキスト（答案用紙の冒頭に問題文として掲載）
# ============================================================

EXAM_TITLE = "現代文（東大型模擬試験）"

# 答案用紙に印刷する問題文（実際の東大では別冊だが、テスト用に同一ページ）
PASSAGE_SUMMARY = (
    "石川哲郎「聴くことの倫理――対話における受動性の意味」による。\n"
    "（本文は省略。以下の設問に解答せよ。）"
)

QUESTIONS = {
    1: {
        "text": (
            "【問一】本文中の空欄（ア）〜（ウ）に入る漢字を楷書で書け。（各2点）\n"
            "（ア）対話の ホンシツ は情報交換にはない\n"
            "（イ）他者の言葉を ジュヨウ する\n"
            "（ウ）自己の認識の ヘンヨウ が始まる"
        ),
    },
    2: {
        "text": (
            "【問二】傍線部(ア)「聴くということは、相手の言葉を自分の了解の枠内に\n"
            "回収する営みではなく、むしろ自分の了解の枠組みそのものが揺さぶられる\n"
            "経験である」とはどういうことか、説明せよ。（2行）"
        ),
    },
    3: {
        "text": (
            "【問三】傍線部(イ)「沈黙は対話の失敗ではなく、対話がもっとも深く\n"
            "成立している瞬間でありうる」のはなぜか、説明せよ。（2行）"
        ),
    },
    4: {
        "text": (
            "【問四】傍線部(ウ)「『わかりやすさ』への志向が、かえって他者との\n"
            "関係を貧しくする」とはどういうことか、説明せよ。（2行）"
        ),
    },
    5: {
        "text": (
            "【問五】筆者は「聴くこと」の意義をどのように捉えているか、\n"
            "本文全体の趣旨を踏まえて120字以内で説明せよ。（4行）"
        ),
    },
}


# ============================================================
# 5名分の答案データ（リアルな得点分布を想定）
# ============================================================

STUDENTS = [
    # ========== 生徒1: 優秀（44/50 想定） ==========
    {
        "name": "中村 陽菜",
        "number": "1",
        "answers": {
            1: {
                "a": "本質",
                "b": "受容",
                "c": "変容",
            },
            2: (
                "聴くとは相手の発言を自分の既有の理解に当てはめて処理すること"
                "ではなく、相手の言葉のなかに自分の予期を超えた意味を見出す"
                "ことで、自己の認識の前提そのものが問い直される経験だということ。"
            ),
            3: (
                "沈黙とは、相手の言葉を十分に受け止めた上で安易にわかった"
                "つもりにならず、「わからなさ」にとどまっている状態であり、"
                "それは他者の他者性を損なわずに向き合う対話の核心的な態度だから。"
            ),
            4: (
                "相手の発言を手早く理解可能な形に整理しようとすることは、"
                "言葉に含まれる多義性や曖昧さという他者の固有性の表れを"
                "切り捨てることであり、真の出会いの可能性を閉ざすということ。"
            ),
            5: (
                "筆者は、聴くことの意義を、相手の言葉を自己の了解の枠内に"
                "取り込むことではなく、自分の理解を超えた他者の言葉の前に"
                "立ち止まり、わからなさを引き受けることに見出している。"
                "この受動的な態度こそが自己の認識の変容を可能にし、"
                "他者と真に出会う対話の条件であると論じている。"
            ),
        },
        "expected_score": "44/50（優秀。各設問の核心を的確に捉えている）",
    },

    # ========== 生徒2: 良好（32/50 想定） ==========
    {
        "name": "小林 蓮",
        "number": "2",
        "answers": {
            1: {
                "a": "本質",
                "b": "受容",
                "c": "変容",
            },
            2: (
                "聴くということは相手の言葉をそのまま受け入れることではなく、"
                "相手の意見によって自分自身の考え方や物の見方が変化するような"
                "体験であるということ。"
            ),
            3: (
                "沈黙は相手の話が理解できないから黙っているのではなく、"
                "相手の言葉を深く受け止めて真剣に考えている状態であり、"
                "それこそ対話において相手を尊重している証拠だから。"
            ),
            4: (
                "相手の言葉をすぐにわかりやすく解釈しようとすると、相手が"
                "本当に伝えたかった微妙なニュアンスを見落としてしまい、"
                "表面的な理解にとどまって深い関係を築けなくなるということ。"
            ),
            5: (
                "筆者は、聴くことは相手を理解しようとすることではなく、"
                "相手の言葉によって自分の考えが変わることに意味があると"
                "捉えている。わかりやすさを求めるのではなく、"
                "わからないことに耐えながら向き合う態度が、"
                "他者との深い関係を築く上で不可欠だと述べている。"
            ),
        },
        "expected_score": "32/50（良好。方向性は合っているが、「了解の枠組み」「他者性」等の核心概念の精度が不足）",
    },

    # ========== 生徒3: 中程度（22/50 想定） ==========
    {
        "name": "渡辺 颯太",
        "number": "3",
        "answers": {
            1: {
                "a": "本質",
                "b": "受容",
                "c": "変容",
            },
            2: (
                "相手の話を聞くときに、自分の知っていることだけで判断するの"
                "ではなく、新しい考えに触れることで自分の考えが変わることが"
                "あるということ。"
            ),
            3: (
                "沈黙しているとき、人は相手の話をじっくり考えていることがあり、"
                "それは対話がうまくいっている状態とも言えるから。"
            ),
            4: (
                "わかりやすさばかりを求めると、相手の複雑な気持ちや考えを"
                "単純にしてしまい、相手のことを本当には理解できなくなって"
                "しまうこと。"
            ),
            5: (
                "聴くことは相手の話を受け入れるだけでなく、自分の考えを"
                "見直すきっかけになる大切な行為である。現代社会では発信"
                "ばかりが重視されるが、聴くことこそ対話の本質であり、"
                "他者との関係を豊かにするものだと筆者は主張している。"
            ),
        },
        "expected_score": "22/50（中程度。大筋は理解しているが、表現が日常語レベルにとどまり、本文の概念を正確に踏まえていない）",
    },

    # ========== 生徒4: やや苦手（14/50 想定） ==========
    {
        "name": "加藤 彩花",
        "number": "4",
        "answers": {
            1: {
                "a": "本質",
                "b": "需要",  # 誤答：「受容」と「需要」の混同
                "c": "変容",
            },
            2: (
                "聴くことは相手の話を理解するだけではなく、自分の意見を"
                "しっかり持つことも大切だということ。コミュニケーションでは"
                "相手の立場に立って考えることが重要である。"
            ),
            3: (
                "対話では言葉だけでなく沈黙も重要なコミュニケーション手段で"
                "あり、沈黙によって相手に対する思いやりや敬意を示すことが"
                "できるから。"
            ),
            4: (
                "難しい話を簡単にしすぎると、大切な内容が伝わらなくなるので、"
                "他者との関係に悪影響があるということ。"
            ),
            5: (
                "聴くことは人間関係においてとても重要だと筆者は述べている。"
                "相手の話をちゃんと聞くことで、相手のことをよく理解でき、"
                "良い関係を築くことができる。現代社会ではSNSなどで発信する"
                "ことが多いが、聴くことの大切さを改めて見直すべきだと思った。"
            ),
        },
        "expected_score": "14/50（やや苦手。本文の議論を日常的な「コミュニケーション論」に読み替えてしまい、筆者の哲学的論点を捉えられていない）",
    },

    # ========== 生徒5: 低得点（5/50 想定） ==========
    {
        "name": "藤田 大和",
        "number": "5",
        "answers": {
            1: {
                "a": "本室",  # 誤答
                "b": "",      # 空欄
                "c": "変用",  # 誤答
            },
            2: "人の話をちゃんと聞くことが大事だということ。",
            3: "沈黙にも意味があるから。",
            4: "",  # 白紙
            5: "聴くことは大切だと筆者は言っている。相手の気持ちを考えることが重要だ。",
        },
        "expected_score": "5/50（低得点。漢字も書けず、論述は字数・内容ともに大幅に不足。問四は白紙）",
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


def create_todai_test_pdf(output_path: str):
    """東大型模擬試験のテスト用答案PDFを生成する（5名分、1名1ページ）"""
    doc = fitz.open()
    font_path = find_japanese_font()

    for student in STUDENTS:
        page = doc.new_page(width=595, height=842)  # A4

        if font_path:
            page.insert_font(fontfile=font_path, fontname="jp")
            fontname = "jp"
        else:
            fontname = "helv"

        y = 35

        # --- ヘッダー ---
        page.insert_text(
            (40, y), EXAM_TITLE,
            fontname=fontname, fontsize=13,
        )
        y += 20
        page.insert_text(
            (40, y), f"受験番号: {student['number']}　　氏名: {student['name']}",
            fontname=fontname, fontsize=10,
        )
        y += 6
        page.draw_line((40, y), (555, y))
        y += 10

        # --- 出典情報 ---
        page.insert_text(
            (40, y), PASSAGE_SUMMARY.split("\n")[0],
            fontname=fontname, fontsize=8, color=(0.4, 0.4, 0.4),
        )
        y += 15

        # --- 問一（漢字） ---
        q1 = QUESTIONS[1]
        for line in q1["text"].split("\n"):
            rect = fitz.Rect(40, y, 555, y + 13)
            page.insert_textbox(rect, line, fontname=fontname, fontsize=9, color=(0.2, 0.2, 0.2))
            y += 13

        y += 5
        answers_1 = student["answers"][1]
        page.insert_text(
            (50, y), "【解答】",
            fontname=fontname, fontsize=9, color=(0, 0, 0.6),
        )
        y += 5
        kanji_text = f"（ア）{answers_1['a']}　　（イ）{answers_1['b'] or '（未記入）'}　　（ウ）{answers_1['c'] or '（未記入）'}"
        kanji_rect = fitz.Rect(55, y, 540, y + 18)
        page.insert_textbox(kanji_rect, kanji_text, fontname=fontname, fontsize=11)
        page.draw_rect(fitz.Rect(50, y - 2, 545, y + 18), color=(0.7, 0.7, 0.7), width=0.5)
        y += 25

        # --- 問二〜問五 ---
        for q_num in [2, 3, 4, 5]:
            q = QUESTIONS[q_num]
            answer = student["answers"][q_num]

            # 問題文（コンパクトに）
            for line in q["text"].split("\n"):
                if not line.strip():
                    y += 5
                    continue
                rect = fitz.Rect(40, y, 555, y + 12)
                page.insert_textbox(rect, line, fontname=fontname, fontsize=8.5, color=(0.2, 0.2, 0.2))
                y += 12

            y += 5

            # 解答欄
            page.insert_text(
                (50, y), "【解答】",
                fontname=fontname, fontsize=9, color=(0, 0, 0.6),
            )
            y += 5

            if answer:
                # 問五は4行（高さ大きめ）、他は2行
                box_height = 85 if q_num == 5 else 50
                answer_rect = fitz.Rect(55, y, 540, y + box_height)
                page.insert_textbox(
                    answer_rect, answer,
                    fontname=fontname, fontsize=10.5,
                )
                page.draw_rect(
                    fitz.Rect(50, y - 2, 545, y + box_height + 2),
                    color=(0.7, 0.7, 0.7), width=0.5,
                )
                y += box_height + 8
            else:
                box_height = 50 if q_num != 5 else 85
                page.insert_text(
                    (60, y + 15), "（未記入）",
                    fontname=fontname, fontsize=10, color=(0.6, 0.6, 0.6),
                )
                page.draw_rect(
                    fitz.Rect(50, y - 2, 545, y + box_height + 2),
                    color=(0.7, 0.7, 0.7), width=0.5,
                )
                y += box_height + 8

        # --- フッター ---
        if y < 810:
            page.draw_line((40, y), (555, y))
            y += 12
            page.insert_text(
                (40, y),
                f"※テスト用データ（想定得点: {student['expected_score'][:student['expected_score'].index('（')]}）",
                fontname=fontname, fontsize=7, color=(0.5, 0.5, 0.5),
            )

    doc.save(output_path)
    doc.close()
    print(f"東大型テスト用PDF を生成しました: {output_path}")
    print(f"  - {len(STUDENTS)}名分（{len(STUDENTS)}ページ）")
    print()
    print("想定得点:")
    for s in STUDENTS:
        print(f"  {s['name']}: {s['expected_score']}")


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    out_dir = Path(__file__).parent / "test_data"
    out_dir.mkdir(exist_ok=True)
    create_todai_test_pdf(str(out_dir / "todai_test_answers.pdf"))
    print(f"\n対応する採点基準: rubrics/todai_rubric.yaml")

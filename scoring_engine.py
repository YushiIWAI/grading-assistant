"""採点エンジン: 複数APIプロバイダー対応の仮採点処理"""

from __future__ import annotations

import json
import random
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from PIL import Image

from typing import Callable

from models import (
    OcrAnswer, Question, QuestionScore, Rubric,
    ScoringSession, StudentOcr, StudentResult,
)
from pdf_processor import image_to_base64


# ============================================================
# 共通プロンプト
# ============================================================

SCORING_SYSTEM_PROMPT = """\
あなたは国語の採点補助AIです。教員の採点作業を支援するため、手書き答案の画像を読み取り、仮採点を行います。

重要な前提:
- あなたの採点は「仮採点」であり、最終判断は教員が行います
- 手書きの読み取りに自信がない場合は、必ず confidence を "low" にし、needs_review を true にしてください
- 部分点の判断が微妙な場合も needs_review を true にしてください
- 読み取れない文字がある場合は transcribed_text に「[判読不明]」と記載してください
"""

OCR_SYSTEM_PROMPT = """\
あなたは手書き答案の読み取り専用AIです。画像から文字を正確に読み取ってください。
採点は行わないでください。読み取りのみを行います。

重要:
- 読み取れない文字がある場合は「[判読不明]」と記載してください
- 空欄の場合は空文字 "" を返してください
- 改行がある場合はそのまま含めてください
"""

HORIZONTAL_GRADING_SYSTEM_PROMPT = """\
あなたは国語の採点補助AIです。複数の学生の解答を同時に評価し、一貫した基準で仮採点します。

重要な前提:
- あなたの採点は「仮採点」であり、最終判断は教員が行います
- 全学生に対して同一の基準を厳密に適用してください
- 学生間の相対的な出来を意識し、一貫性のある採点を行ってください
- 本文のキーワードや概念を正確に用いた説明と、日常語による表面的な言い換えを明確に区別してください
- 部分点の判断が微妙な場合は needs_review を true にしてください
"""


def build_scoring_prompt(
    rubric: Rubric,
    reference_students: list[StudentResult] | None = None,
) -> str:
    """採点基準からプロンプトを構築する。参考例があれば含める。"""
    lines = [
        f"# 試験: {rubric.title}",
        f"満点: {rubric.total_points}点",
        "",
        "# 採点基準",
    ]

    if rubric.notes:
        lines.append(f"\n## 採点上の注意\n{rubric.notes}\n")

    for q in rubric.questions:
        lines.append(f"\n## 問{q.id}: {q.description}")
        lines.append(f"- 種別: {q.question_type}")
        lines.append(f"- 配点: {q.max_points}点")

        if q.sub_questions:
            lines.append("- 小問:")
            for sq in q.sub_questions:
                lines.append(f"  - {sq.id}: {sq.text} → 正答「{sq.answer}」({sq.points}点)")

        if q.model_answer:
            lines.append(f"- 模範解答: {q.model_answer}")

        if q.scoring_criteria:
            lines.append(f"- 採点基準:\n{q.scoring_criteria}")

    # --- 教員の採点例（キャリブレーション） ---
    if reference_students:
        lines.extend([
            "",
            "# 教員の採点例（重要: この採点傾向に合わせてください）",
            "以下は、担当教員が実際につけた点数とコメントです。",
            "この教員の採点基準の解釈・厳しさの程度を参考にして、一貫した基準で採点してください。",
        ])
        for ref in reference_students:
            lines.append(f"\n## 採点例: {ref.student_name or ref.student_id}")
            for qs in ref.question_scores:
                lines.append(f"- 問{qs.question_id}:")
                if qs.transcribed_text:
                    lines.append(f"  解答: 「{qs.transcribed_text}」")
                lines.append(f"  教員の採点: {qs.score}/{qs.max_points}点")
                if qs.comment:
                    lines.append(f"  教員のコメント: {qs.comment}")
            if ref.reviewer_notes:
                lines.append(f"  教員メモ: {ref.reviewer_notes}")

    # --- 回答形式 ---
    lines.extend([
        "",
        "# 回答形式",
        "以下のJSON形式で回答してください。JSONのみを出力し、他のテキストは含めないでください。",
        "",
        '```json',
        '{',
        '  "student_name": "読み取れた氏名（不明なら空文字）",',
        '  "scores": [',
        '    {',
        '      "question_id": "設問ID（例: \"1\" or \"1-1\"）",',
        '      "score": 得点(数値),',
        '      "max_points": 配点(数値),',
        '      "transcribed_text": "読み取った解答テキスト",',
        '      "comment": "採点の根拠や補足",',
        '      "confidence": "high/medium/low",',
        '      "needs_review": true/false',
        '    }',
        '  ],',
        '  "overall_comment": "答案全体に対するコメント"',
        '}',
        '```',
    ])

    return "\n".join(lines)


def _extract_json(text: str | None) -> dict:
    """レスポンステキストからJSONを抽出する（修復ロジック付き）"""
    if text is None:
        raise ValueError(
            "APIからの応答テキストが空です。"
            "思考トークンで出力上限に達した可能性があります。"
        )
    # コードブロックからJSON部分を抽出
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    # まず素直にパース
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 修復を試行: よくあるLLMのJSON不正パターン
        fixed = text
        # 末尾に余計なテキストが付いている場合、最後の } or ] で切る
        last_brace = fixed.rfind("}")
        last_bracket = fixed.rfind("]")
        cut_pos = max(last_brace, last_bracket)
        if cut_pos > 0:
            fixed = fixed[: cut_pos + 1]
        # 末尾カンマ除去 (,} や ,] のパターン)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        parsed = json.loads(fixed)  # これでもダメならそのまま例外

    # APIがリスト形式で返した場合、最初の要素を取得
    if isinstance(parsed, list):
        if len(parsed) == 0:
            raise ValueError("APIが空のJSON配列を返しました。")
        parsed = parsed[0]
    return parsed


def _api_call_with_retry(api_fn, max_retries: int = 2, delay: float = 2.0):
    """APIコール + JSONパースをリトライ付きで実行する。

    api_fn: 呼び出すと (response_text: str) を返す関数
    戻り値: パース済みdict
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response_text = api_fn()
            return _extract_json(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(delay * (attempt + 1))
                continue
            raise ValueError(
                f"JSON解析に{max_retries + 1}回失敗しました: {last_error}"
            ) from last_error


# ============================================================
# Phase 1: OCR（読み取り専用）
# ============================================================

def build_ocr_prompt(rubric: Rubric) -> str:
    """OCR用プロンプト: 画像から氏名と各問の解答テキストだけ抽出する"""
    lines = [
        f"# 試験: {rubric.title}",
        "",
        "以下の答案画像から、学生の氏名と各問の解答テキストを読み取ってください。",
        "採点は不要です。テキストの読み取りのみ行ってください。",
        "",
        "# 読み取る設問一覧",
    ]
    # 設問タイプの日本語表記
    TYPE_HINTS = {
        "short_answer": "短答（語句・漢字の読みなど短い回答）",
        "descriptive": "記述（文章での回答）",
        "selection": "選択（記号や番号での回答）",
    }

    all_ids = []
    for q in rubric.questions:
        type_hint = TYPE_HINTS.get(q.question_type, q.question_type)
        if q.sub_questions:
            lines.append(f"\n### 問{q.id}（{type_hint}）")
            for sq in q.sub_questions:
                lines.append(f"- 設問ID \"{sq.id}\": {sq.text}（期待: 短い語句）")
                all_ids.append(sq.id)
        else:
            desc = q.description.strip().replace("\n", " ")
            expected = "文章での記述" if q.question_type == "descriptive" else "短い語句"
            lines.append(f"- 設問ID \"{q.id}\": {desc[:60]}（{type_hint}、期待: {expected}）")
            all_ids.append(str(q.id))

    # JSON例を具体的IDで示す
    example_answers = ",\n    ".join(
        f'{{"question_id": "{aid}", "transcribed_text": "...", "confidence": "high"}}'
        for aid in all_ids[:3]
    )
    lines.extend([
        "",
        "# 回答形式",
        "以下のJSON形式で回答してください。JSONのみを出力してください。",
        f"question_id は必ず上記の設問ID（{', '.join(repr(a) for a in all_ids)}）をそのまま使ってください。",
        "",
        "```json",
        "{",
        '  "student_name": "読み取れた氏名（不明なら空文字）",',
        '  "answers": [',
        f"    {example_answers}",
        '  ]',
        "}",
        "```",
    ])
    return "\n".join(lines)


def parse_ocr_result(
    result: dict, rubric: Rubric,
) -> tuple[str, list[OcrAnswer]]:
    """OCR API結果をパースする。Returns: (student_name, answers)"""
    student_name = result.get("student_name", "")

    expected_ids: set[str] = set()
    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                expected_ids.add(str(sq.id))
        else:
            expected_ids.add(str(q.id))

    answers: list[OcrAnswer] = []
    for a in result.get("answers", []):
        qid = str(a.get("question_id", ""))
        # 「問2」→「2」のように "問" プレフィックスを正規化
        if qid not in expected_ids and qid.startswith("問"):
            qid = qid[1:]
        if qid not in expected_ids:
            continue
        answers.append(OcrAnswer(
            question_id=qid,
            transcribed_text=a.get("transcribed_text", ""),
            confidence=a.get("confidence", "medium"),
        ))

    # 欠落IDは空テキストで補完
    found_ids = {a.question_id for a in answers}
    for eid in expected_ids - found_ids:
        answers.append(OcrAnswer(
            question_id=eid, transcribed_text="", confidence="low",
        ))

    return student_name, answers


# ============================================================
# Phase 2: 横断採点（テキストのみ、画像不要）
# ============================================================

DEFAULT_BATCH_SIZE = 15


def recommend_batch_size(rubric: Rubric) -> tuple[int, str]:
    """ルーブリック内容からバッチサイズの推奨値を算出する。

    Returns:
        (recommended_size, reason)
    """
    descriptive_count = sum(
        1 for q in rubric.questions if q.question_type == "descriptive"
    )
    short_answer_count = sum(
        1 for q in rubric.questions if q.question_type != "descriptive"
    )
    total_sub_questions = sum(
        len(q.sub_questions) for q in rubric.questions if q.sub_questions
    )

    # 記述問題が多いほどバッチは小さく
    if descriptive_count >= 3:
        size = 8
        reason = f"記述問題が{descriptive_count}問あるため小さめ推奨"
    elif descriptive_count >= 1:
        # 記述が1-2問、小問の多さも考慮
        if total_sub_questions > 10:
            size = 12
            reason = f"記述{descriptive_count}問 + 小問{total_sub_questions}問で中程度"
        else:
            size = 12
            reason = f"記述{descriptive_count}問を含むため中程度推奨"
    else:
        # 短答のみ
        if total_sub_questions > 15:
            size = 18
            reason = f"短答のみ（小問{total_sub_questions}問）のため大きめ可能"
        else:
            size = 20
            reason = f"短答{short_answer_count}問のみのため大きめ推奨"

    return size, reason


def build_horizontal_grading_prompt(
    question: Question,
    rubric_title: str,
    students_answers: list[tuple[str, str, str]],
    reference_info: list[dict] | None = None,
    notes: str = "",
) -> str:
    """横断採点プロンプト: 1問に対して複数学生の解答を同時に採点する。

    Args:
        students_answers: 各学生の (student_id, student_name, transcribed_text) リスト
    """
    lines = [
        f"# 試験: {rubric_title}",
        "",
        f"## 問{question.id}: {question.description.strip()}",
        f"- 種別: {question.question_type}",
        f"- 配点: {question.max_points}点",
    ]

    if question.sub_questions:
        lines.append("- 小問:")
        for sq in question.sub_questions:
            lines.append(f"  - {sq.id}: {sq.text} → 正答「{sq.answer}」({sq.points}点)")

    if question.model_answer:
        lines.append(f"\n### 模範解答\n{question.model_answer.strip()}")

    if question.scoring_criteria:
        lines.append(f"\n### 採点基準\n{question.scoring_criteria.strip()}")

    if notes:
        lines.append(f"\n## 採点上の注意\n{notes.strip()}")

    if reference_info:
        lines.extend([
            "",
            "## 教員の採点例（この設問について）",
            "以下の教員の採点傾向に合わせてください。",
        ])
        for ref in reference_info:
            qs = ref["score"]
            lines.append(f"\n### 採点例: {ref['student_name']}")
            if qs.transcribed_text:
                lines.append(f"  解答: 「{qs.transcribed_text}」")
            lines.append(f"  教員の採点: {qs.score}/{qs.max_points}点")
            if qs.comment:
                lines.append(f"  教員のコメント: {qs.comment}")

    lines.extend([
        "",
        f"## 採点対象の解答一覧（{len(students_answers)}名分）",
        "以下の全ての解答を同じ基準で公平に採点してください。",
    ])
    for sid, sname, text in students_answers:
        display_name = sname or sid
        lines.append(f"\n### {sid}（{display_name}）の解答:")
        if text.strip():
            lines.append(f"「{text}」")
        else:
            lines.append("（空欄）")

    # JSON応答フォーマット
    if question.sub_questions:
        score_fmt = (
            '      "scores": [\n'
            '        {"question_id": "小問ID", "score": 得点, "max_points": 配点, '
            '"comment": "採点根拠", "confidence": "high/medium/low", "needs_review": true/false}\n'
            "      ]"
        )
    else:
        score_fmt = (
            f'      "question_id": "{question.id}",\n'
            '      "score": 得点,\n'
            f'      "max_points": {question.max_points},\n'
            '      "comment": "採点の根拠",\n'
            '      "confidence": "high/medium/low",\n'
            '      "needs_review": true/false'
        )

    lines.extend([
        "",
        "## 回答形式",
        "以下のJSON形式で全学生分の採点結果を返してください。JSONのみを出力してください。",
        "",
        "```json",
        "{",
        '  "results": [',
        "    {",
        '      "student_id": "学生ID",',
        score_fmt,
        "    }",
        "  ]",
        "}",
        "```",
    ])

    return "\n".join(lines)


def parse_horizontal_grading_result(
    result: dict,
    question: Question,
    expected_student_ids: list[str],
) -> dict[str, list[QuestionScore]]:
    """横断採点結果をパースする。Returns: dict[student_id, list[QuestionScore]]"""
    scores_by_student: dict[str, list[QuestionScore]] = {}

    # 小問IDごとの配点マップ（クランプ用）
    sub_points_map = {}
    if question.sub_questions:
        for sq in question.sub_questions:
            sub_points_map[str(sq.id)] = float(sq.points)

    for entry in result.get("results", []):
        sid = entry.get("student_id", "")
        if sid not in expected_student_ids:
            continue

        if question.sub_questions:
            expected_sub_ids = {str(sq.id) for sq in question.sub_questions}
            scores = []
            for s in entry.get("scores", []):
                qid = str(s.get("question_id", ""))
                if qid not in expected_sub_ids:
                    continue
                mp = sub_points_map.get(qid, float(s.get("max_points", 0)))
                raw_score = float(s.get("score", 0))
                scores.append(QuestionScore(
                    question_id=qid,
                    score=max(0.0, min(raw_score, mp)),
                    max_points=mp,
                    transcribed_text="",
                    comment=s.get("comment", ""),
                    confidence=s.get("confidence", "medium"),
                    needs_review=s.get("needs_review", False),
                ))
            scores_by_student[sid] = scores
        else:
            mp = float(question.max_points)
            raw_score = float(entry.get("score", 0))
            scores_by_student[sid] = [QuestionScore(
                question_id=str(entry.get("question_id", question.id)),
                score=max(0.0, min(raw_score, mp)),
                max_points=mp,
                transcribed_text="",
                comment=entry.get("comment", ""),
                confidence=entry.get("confidence", "medium"),
                needs_review=entry.get("needs_review", False),
            )]

    # 欠落学生にプレースホルダー
    for sid in expected_student_ids:
        if sid not in scores_by_student:
            if question.sub_questions:
                scores_by_student[sid] = [
                    QuestionScore(
                        question_id=sq.id, score=0, max_points=sq.points,
                        comment="API応答に含まれていません", confidence="low", needs_review=True,
                    )
                    for sq in question.sub_questions
                ]
            else:
                scores_by_student[sid] = [QuestionScore(
                    question_id=str(question.id), score=0, max_points=question.max_points,
                    comment="API応答に含まれていません", confidence="low", needs_review=True,
                )]

    return scores_by_student


# ============================================================
# 従来方式のパーサー（後方互換）
# ============================================================

def parse_scoring_result(result: dict) -> tuple[str, list[QuestionScore], str]:
    """API結果をモデルオブジェクトに変換する"""
    student_name = result.get("student_name", "")
    overall_comment = result.get("overall_comment", "")

    scores = []
    for s in result.get("scores", []):
        mp = float(s.get("max_points", 0))
        raw_score = float(s.get("score", 0))
        scores.append(QuestionScore(
            question_id=str(s.get("question_id", "")),
            score=max(0.0, min(raw_score, mp)) if mp > 0 else raw_score,
            max_points=mp,
            transcribed_text=s.get("transcribed_text", ""),
            comment=s.get("comment", ""),
            confidence=s.get("confidence", "medium"),
            needs_review=s.get("needs_review", False),
        ))

    return student_name, scores, overall_comment


# ============================================================
# 設問単位の採点（分割アーキテクチャ）
# ============================================================

def build_single_question_prompt(
    question: Question,
    rubric_title: str,
    extract_student_name: bool = False,
    reference_students_info: list[dict] | None = None,
    notes: str = "",
) -> str:
    """1問分の採点プロンプトを構築する。"""
    lines = [
        f"# 試験: {rubric_title}",
        "",
        f"## 問{question.id}: {question.description}",
        f"- 種別: {question.question_type}",
        f"- 配点: {question.max_points}点",
    ]

    if question.sub_questions:
        lines.append("- 小問:")
        for sq in question.sub_questions:
            lines.append(f"  - {sq.id}: {sq.text} → 正答「{sq.answer}」({sq.points}点)")

    if question.model_answer:
        lines.append(f"- 模範解答: {question.model_answer}")

    if question.scoring_criteria:
        lines.append(f"- 採点基準:\n{question.scoring_criteria}")

    if notes:
        lines.append(f"\n## 採点上の注意\n{notes}")

    # --- 教員の採点例（この設問のみ） ---
    if reference_students_info:
        lines.extend([
            "",
            "# 教員の採点例（この設問について）",
            "以下の教員の採点傾向に合わせてください。",
        ])
        for ref in reference_students_info:
            qs = ref["score"]
            lines.append(f"\n### 採点例: {ref['student_name']}")
            if qs.transcribed_text:
                lines.append(f"  解答: 「{qs.transcribed_text}」")
            lines.append(f"  教員の採点: {qs.score}/{qs.max_points}点")
            if qs.comment:
                lines.append(f"  教員のコメント: {qs.comment}")

    # --- 回答形式 ---
    lines.extend(["", "# 回答形式", "以下のJSON形式で回答してください。JSONのみを出力してください。", ""])

    if question.sub_questions:
        # 小問あり: scores 配列で返す
        lines.append('```json')
        lines.append('{')
        if extract_student_name:
            lines.append('  "student_name": "読み取れた氏名（不明なら空文字）",')
        lines.append('  "scores": [')
        lines.append('    {')
        lines.append('      "question_id": "小問ID",')
        lines.append('      "score": 得点,')
        lines.append('      "max_points": 配点,')
        lines.append('      "transcribed_text": "読み取った解答",')
        lines.append('      "comment": "採点根拠",')
        lines.append('      "confidence": "high/medium/low",')
        lines.append('      "needs_review": true/false')
        lines.append('    }')
        lines.append('  ]')
        lines.append('}')
        lines.append('```')
    else:
        # 単一問題
        lines.append('```json')
        lines.append('{')
        if extract_student_name:
            lines.append('  "student_name": "読み取れた氏名（不明なら空文字）",')
        lines.append(f'  "question_id": "{question.id}",')
        lines.append('  "score": 得点,')
        lines.append(f'  "max_points": {question.max_points},')
        lines.append('  "transcribed_text": "読み取った解答テキスト",')
        lines.append('  "comment": "採点の根拠や補足",')
        lines.append('  "confidence": "high/medium/low",')
        lines.append('  "needs_review": true/false')
        lines.append('}')
        lines.append('```')

    return "\n".join(lines)


def parse_single_question_result(
    result: dict,
    question: Question,
) -> tuple[str, list[QuestionScore]]:
    """1問分のAPI結果をパースする。

    Returns:
        (student_name, question_scores)
    """
    student_name = result.get("student_name", "")
    scores = []

    if question.sub_questions:
        # 期待される小問IDのセット（APIが余分な設問を返した場合に除外する）
        sub_points_map = {str(sq.id): float(sq.points) for sq in question.sub_questions}
        expected_ids = set(sub_points_map.keys())
        for s in result.get("scores", []):
            qid = str(s.get("question_id", ""))
            if qid not in expected_ids:
                continue  # この設問に属さないスコアは無視
            mp = sub_points_map.get(qid, float(s.get("max_points", 0)))
            raw_score = float(s.get("score", 0))
            scores.append(QuestionScore(
                question_id=qid,
                score=max(0.0, min(raw_score, mp)),
                max_points=mp,
                transcribed_text=s.get("transcribed_text", ""),
                comment=s.get("comment", ""),
                confidence=s.get("confidence", "medium"),
                needs_review=s.get("needs_review", False),
            ))
    else:
        mp = float(question.max_points)
        raw_score = float(result.get("score", 0))
        scores.append(QuestionScore(
            question_id=str(result.get("question_id", question.id)),
            score=max(0.0, min(raw_score, mp)),
            max_points=mp,
            transcribed_text=result.get("transcribed_text", ""),
            comment=result.get("comment", ""),
            confidence=result.get("confidence", "medium"),
            needs_review=result.get("needs_review", False),
        ))

    return student_name, scores


def _build_reference_for_question(
    reference_students: list[StudentResult],
    question: Question,
) -> list[dict] | None:
    """参考例から指定された設問の採点情報のみを抽出する。"""
    result = []
    target_ids: set[str] = set()

    if question.sub_questions:
        target_ids = {sq.id for sq in question.sub_questions}
    else:
        target_ids = {str(question.id)}

    for ref in reference_students:
        matching = [qs for qs in ref.question_scores if qs.question_id in target_ids]
        for qs in matching:
            result.append({
                "student_name": ref.student_name or ref.student_id,
                "score": qs,
            })

    return result if result else None


def score_student_by_question(
    provider: "ScoringProvider",
    images: list[Image.Image],
    rubric: Rubric,
    reference_students: list[StudentResult] | None = None,
    on_question_scored: Callable[[int, int, Question], None] | None = None,
) -> tuple[str, list[QuestionScore], str, list[str]]:
    """設問ごとにAPIを呼び出して1学生分を採点する。

    Returns:
        (student_name, question_scores, overall_comment, errors)
    """
    student_name = ""
    all_scores: list[QuestionScore] = []
    errors: list[str] = []

    for q_idx, question in enumerate(rubric.questions):
        is_first = (q_idx == 0)

        if on_question_scored:
            on_question_scored(q_idx, len(rubric.questions), question)

        ref_info = None
        if reference_students:
            ref_info = _build_reference_for_question(reference_students, question)

        try:
            result = provider.score_question(
                images=images,
                question=question,
                rubric_title=rubric.title,
                extract_student_name=is_first,
                reference_students_info=ref_info,
                notes=rubric.notes,
            )

            name, scores = parse_single_question_result(result, question)
            if name:
                student_name = name
            all_scores.extend(scores)

        except Exception as e:
            error_msg = f"問{question.id}: {e}"
            errors.append(error_msg)
            # プレースホルダーを挿入して続行
            if question.sub_questions:
                for sq in question.sub_questions:
                    all_scores.append(QuestionScore(
                        question_id=sq.id, score=0, max_points=sq.points,
                        comment=f"採点エラー: {e}", confidence="low", needs_review=True,
                    ))
            else:
                all_scores.append(QuestionScore(
                    question_id=str(question.id), score=0, max_points=question.max_points,
                    comment=f"採点エラー: {e}", confidence="low", needs_review=True,
                ))

    overall_comment = ""
    if errors:
        overall_comment = f"[{len(errors)}問で採点エラー発生]"

    return student_name, all_scores, overall_comment, errors


# ============================================================
# Phase 1 オーケストレーション: OCR
# ============================================================

def ocr_all_students(
    provider: "ScoringProvider",
    student_groups: list[list[tuple[int, Image.Image]]],
    rubric: Rubric,
    on_student_ocr: Callable[[int, int], None] | None = None,
) -> tuple[list[StudentOcr], list[str]]:
    """Phase 1: 全学生のOCRを実行する。"""
    ocr_results: list[StudentOcr] = []
    errors: list[str] = []

    for i, group in enumerate(student_groups):
        student_num = i + 1
        student_id = f"S{student_num:03d}"

        if on_student_ocr:
            on_student_ocr(i, len(student_groups))

        group_images = [img for _, img in group]
        page_numbers = [pn for pn, _ in group]

        try:
            result = provider.ocr_student(images=group_images, rubric=rubric)
            name, answers = parse_ocr_result(result, rubric)

            ocr_results.append(StudentOcr(
                student_id=student_id,
                student_name=name,
                page_numbers=page_numbers,
                answers=answers,
                status="ocr_done",
            ))
        except Exception as e:
            error_msg = f"学生{student_num}: OCRエラー - {e}"
            errors.append(error_msg)
            ocr_results.append(StudentOcr(
                student_id=student_id,
                page_numbers=page_numbers,
                status="pending",
                ocr_error=str(e),
            ))

    return ocr_results, errors


# ============================================================
# Phase 2 オーケストレーション: 横断採点
# ============================================================

def grade_question_horizontally(
    provider: "ScoringProvider",
    question: Question,
    rubric_title: str,
    all_students_answers: list[tuple[str, str, str]],
    reference_info: list[dict] | None = None,
    notes: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[dict[str, list[QuestionScore]], list[str]]:
    """1問を全学生分、バッチで横断採点する。"""
    all_scores: dict[str, list[QuestionScore]] = {}
    errors: list[str] = []

    batches = [
        all_students_answers[i:i + batch_size]
        for i in range(0, len(all_students_answers), batch_size)
    ]

    for batch_idx, batch in enumerate(batches):
        expected_ids = [sid for sid, _, _ in batch]

        try:
            result = provider.grade_question_batch(
                question=question,
                rubric_title=rubric_title,
                students_answers=batch,
                reference_info=reference_info,
                notes=notes,
            )
            batch_scores = parse_horizontal_grading_result(result, question, expected_ids)
            all_scores.update(batch_scores)
        except Exception as e:
            error_msg = f"問{question.id} バッチ{batch_idx + 1}/{len(batches)}: {e}"
            errors.append(error_msg)
            for sid, _, _ in batch:
                if question.sub_questions:
                    all_scores[sid] = [
                        QuestionScore(
                            question_id=sq.id, score=0, max_points=sq.points,
                            comment=f"採点エラー: {e}", confidence="low", needs_review=True,
                        )
                        for sq in question.sub_questions
                    ]
                else:
                    all_scores[sid] = [QuestionScore(
                        question_id=str(question.id), score=0, max_points=question.max_points,
                        comment=f"採点エラー: {e}", confidence="low", needs_review=True,
                    )]

    return all_scores, errors


def run_horizontal_grading(
    provider: "ScoringProvider",
    rubric: Rubric,
    session: ScoringSession,
    reference_students: list[StudentResult] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_question_progress: Callable[[int, int, Question, int, int], None] | None = None,
    student_ids_to_grade: list[str] | None = None,
) -> list[str]:
    """Phase 2 全体: OCR結果を使って全問を横断採点する。"""
    all_errors: list[str] = []

    target_ids = student_ids_to_grade
    if target_ids is None:
        target_ids = [
            o.student_id for o in session.ocr_results
            if o.status in ("ocr_done", "reviewed")
        ]

    # StudentResult が未作成の学生を作成
    existing_ids = {s.student_id for s in session.students}
    for ocr in session.ocr_results:
        if ocr.student_id in target_ids and ocr.student_id not in existing_ids:
            session.students.append(StudentResult(
                student_id=ocr.student_id,
                student_name=ocr.student_name,
                page_numbers=ocr.page_numbers,
                status="pending",
            ))

    for q_idx, question in enumerate(rubric.questions):
        # この設問のID一覧
        question_ids: list[str] = (
            [sq.id for sq in question.sub_questions] if question.sub_questions
            else [str(question.id)]
        )

        # 全対象学生の解答テキストを収集
        students_answers: list[tuple[str, str, str]] = []
        for sid in target_ids:
            ocr = session.get_ocr_for_student(sid)
            if not ocr:
                continue
            texts = []
            for qid in question_ids:
                for ans in ocr.answers:
                    if ans.question_id == qid:
                        texts.append(ans.transcribed_text)
            combined = "\n".join(texts) if len(texts) > 1 else (texts[0] if texts else "")
            students_answers.append((sid, ocr.student_name or sid, combined))

        if not students_answers:
            continue

        ref_info = None
        if reference_students:
            ref_info = _build_reference_for_question(reference_students, question)

        n_batches = (len(students_answers) + batch_size - 1) // batch_size

        if on_question_progress:
            on_question_progress(q_idx, len(rubric.questions), question, 0, n_batches)

        scores_by_student, errors = grade_question_horizontally(
            provider=provider,
            question=question,
            rubric_title=rubric.title,
            all_students_answers=students_answers,
            reference_info=ref_info,
            notes=rubric.notes,
            batch_size=batch_size,
        )
        all_errors.extend(errors)

        # 結果を StudentResult にマージ
        for sid, q_scores in scores_by_student.items():
            student = next((s for s in session.students if s.student_id == sid), None)
            if not student:
                continue

            # transcribed_text を OCR データから補完
            ocr = session.get_ocr_for_student(sid)
            if ocr:
                for qs in q_scores:
                    for ans in ocr.answers:
                        if ans.question_id == qs.question_id:
                            qs.transcribed_text = ans.transcribed_text
                            break

            # 旧スコアを除去して新スコアを追加
            old_qids = set(question_ids)
            student.question_scores = [
                qs for qs in student.question_scores
                if qs.question_id not in old_qids
            ]
            student.question_scores.extend(q_scores)

        if on_question_progress:
            on_question_progress(q_idx, len(rubric.questions), question, n_batches, n_batches)

    # 最終処理
    for student in session.students:
        if student.student_id in target_ids:
            student.recalculate_total()
            student.status = "ai_scored"

    session.grading_mode = "horizontal"
    return all_errors


# ============================================================
# プロバイダー抽象クラス
# ============================================================

class ScoringProvider(ABC):
    """採点プロバイダーの基底クラス"""

    @abstractmethod
    def score_student(
        self,
        images: list[Image.Image],
        rubric: Rubric,
        reference_students: list[StudentResult] | None = None,
    ) -> dict:
        """学生の答案を全問まとめて仮採点し、結果dictを返す（従来方式）"""
        pass

    def score_question(
        self,
        images: list[Image.Image],
        question: Question,
        rubric_title: str,
        extract_student_name: bool = False,
        reference_students_info: list[dict] | None = None,
        notes: str = "",
    ) -> dict:
        """学生の答案から1問分を仮採点し、結果dictを返す（設問分割方式）"""
        raise NotImplementedError(
            f"{self.__class__.__name__} は設問単位の採点に未対応です"
        )

    def ocr_student(
        self,
        images: list[Image.Image],
        rubric: Rubric,
    ) -> dict:
        """学生の答案画像からテキストのみ読み取る（Phase 1）"""
        raise NotImplementedError(
            f"{self.__class__.__name__} はOCR読み取りに未対応です"
        )

    def grade_question_batch(
        self,
        question: Question,
        rubric_title: str,
        students_answers: list[tuple[str, str, str]],
        reference_info: list[dict] | None = None,
        notes: str = "",
    ) -> dict:
        """1問を複数学生分まとめて横断採点する（Phase 2）。テキストのみ、画像不要。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} は横断採点に未対応です"
        )

    @property
    @abstractmethod
    def name(self) -> str:
        pass


# ============================================================
# Gemini プロバイダー
# ============================================================

class GeminiProvider(ScoringProvider):
    """Google Gemini APIによる採点"""

    MODELS = {
        "gemini-2.5-flash": "Gemini 2.5 Flash（高速・低コスト）",
        "gemini-2.5-pro": "Gemini 2.5 Pro（高精度）",
    }

    TIMEOUT = 120  # seconds

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def _call_with_timeout(self, fn):
        """ThreadPoolExecutorでGemini API呼び出しにタイムアウトを設定する"""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            try:
                return future.result(timeout=self.TIMEOUT)
            except FuturesTimeoutError:
                raise TimeoutError(
                    f"Gemini APIが{self.TIMEOUT}秒以内に応答しませんでした"
                )

    @property
    def name(self) -> str:
        return f"Gemini ({self.model_name})"

    def score_student(
        self,
        images: list[Image.Image],
        rubric: Rubric,
        reference_students: list[StudentResult] | None = None,
    ) -> dict:
        from google.genai import types

        prompt = SCORING_SYSTEM_PROMPT + "\n\n" + build_scoring_prompt(rubric, reference_students)

        contents = []
        for i, img in enumerate(images):
            contents.append(img)
            if len(images) > 1:
                contents.append(f"（上記は答案の{i + 1}ページ目です）")
        contents.append(prompt)

        def _call():
            response = self._call_with_timeout(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=65536,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=8192,
                        ),
                    ),
                )
            )
            return _gemini_extract_text(response)

        return _api_call_with_retry(_call)

    def score_question(
        self,
        images: list[Image.Image],
        question: Question,
        rubric_title: str,
        extract_student_name: bool = False,
        reference_students_info: list[dict] | None = None,
        notes: str = "",
    ) -> dict:
        from google.genai import types

        prompt = (
            SCORING_SYSTEM_PROMPT + "\n\n"
            + build_single_question_prompt(
                question=question, rubric_title=rubric_title,
                extract_student_name=extract_student_name,
                reference_students_info=reference_students_info,
                notes=notes,
            )
        )

        contents = []
        for i, img in enumerate(images):
            contents.append(img)
            if len(images) > 1:
                contents.append(f"（上記は答案の{i + 1}ページ目です）")
        contents.append(prompt)

        def _call():
            response = self._call_with_timeout(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=24576,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=16384,
                        ),
                    ),
                )
            )
            return _gemini_extract_text(response)

        return _api_call_with_retry(_call)

    def ocr_student(self, images, rubric):
        from google.genai import types

        prompt = OCR_SYSTEM_PROMPT + "\n\n" + build_ocr_prompt(rubric)

        contents = []
        for i, img in enumerate(images):
            contents.append(img)
            if len(images) > 1:
                contents.append(f"（上記は答案の{i + 1}ページ目です）")
        contents.append(prompt)

        def _call():
            response = self._call_with_timeout(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=4096,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=2048,
                        ),
                    ),
                )
            )
            return _gemini_extract_text(response)

        return _api_call_with_retry(_call)

    def grade_question_batch(self, question, rubric_title,
                              students_answers, reference_info=None, notes=""):
        from google.genai import types

        prompt = (
            HORIZONTAL_GRADING_SYSTEM_PROMPT + "\n\n"
            + build_horizontal_grading_prompt(
                question=question, rubric_title=rubric_title,
                students_answers=students_answers,
                reference_info=reference_info, notes=notes,
            )
        )

        n = len(students_answers)
        thinking_budget = min(4096 + n * 256, 16384)
        response_budget = max(4096, n * 1024)
        max_output = min(thinking_budget + response_budget, 65536)

        def _call():
            response = self._call_with_timeout(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=max_output,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=thinking_budget,
                        ),
                    ),
                )
            )
            return _gemini_extract_text(response)

        return _api_call_with_retry(_call)


def _gemini_extract_text(response) -> str | None:
    """Gemini レスポンスからテキストを取得する。思考モデル対応。"""
    text = response.text
    if text is None:
        for candidate in (response.candidates or []):
            for part in (candidate.content.parts or []):
                if part.text and not getattr(part, "thought", False):
                    text = part.text
                    break
            if text:
                break
    return text


# ============================================================
# Anthropic プロバイダー
# ============================================================

class AnthropicProvider(ScoringProvider):
    """Anthropic Claude APIによる採点"""

    MODELS = {
        "claude-sonnet-4-20250514": "Claude Sonnet 4（バランス型）",
        "claude-haiku-4-20250414": "Claude Haiku 4.5（高速・低コスト）",
    }

    def __init__(self, api_key: str, model_name: str = "claude-sonnet-4-20250514"):
        import anthropic
        self.client = anthropic.Anthropic(
            api_key=api_key,
            timeout=120.0,
        )
        self.model_name = model_name

    @property
    def name(self) -> str:
        return f"Claude ({self.model_name})"

    def score_student(
        self,
        images: list[Image.Image],
        rubric: Rubric,
        reference_students: list[StudentResult] | None = None,
    ) -> dict:
        scoring_prompt = build_scoring_prompt(rubric, reference_students)

        content = []
        for i, img in enumerate(images):
            b64 = image_to_base64(img)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })
            if len(images) > 1:
                content.append({
                    "type": "text",
                    "text": f"（上記は答案の{i + 1}ページ目です）",
                })

        content.append({"type": "text", "text": scoring_prompt})

        def _call():
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=4096,
                temperature=0.2,
                system=SCORING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text

        return _api_call_with_retry(_call)

    def score_question(
        self,
        images: list[Image.Image],
        question: Question,
        rubric_title: str,
        extract_student_name: bool = False,
        reference_students_info: list[dict] | None = None,
        notes: str = "",
    ) -> dict:
        scoring_prompt = build_single_question_prompt(
            question=question, rubric_title=rubric_title,
            extract_student_name=extract_student_name,
            reference_students_info=reference_students_info,
            notes=notes,
        )

        content = []
        for i, img in enumerate(images):
            b64 = image_to_base64(img)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
            if len(images) > 1:
                content.append({"type": "text", "text": f"（上記は答案の{i + 1}ページ目です）"})
        content.append({"type": "text", "text": scoring_prompt})

        def _call():
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=2048,
                temperature=0.2,
                system=SCORING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text

        return _api_call_with_retry(_call)

    def ocr_student(self, images, rubric):
        prompt = build_ocr_prompt(rubric)

        content = []
        for i, img in enumerate(images):
            b64 = image_to_base64(img)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
            if len(images) > 1:
                content.append({"type": "text", "text": f"（上記は答案の{i + 1}ページ目です）"})
        content.append({"type": "text", "text": prompt})

        def _call():
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=4096,
                temperature=0.1,
                system=OCR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text

        return _api_call_with_retry(_call)

    def grade_question_batch(self, question, rubric_title,
                              students_answers, reference_info=None, notes=""):
        prompt = build_horizontal_grading_prompt(
            question=question, rubric_title=rubric_title,
            students_answers=students_answers,
            reference_info=reference_info, notes=notes,
        )

        n = len(students_answers)
        max_tokens = min(2048 + n * 256, 16384)

        def _call():
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=max_tokens,
                temperature=0.2,
                system=HORIZONTAL_GRADING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            )
            return response.content[0].text

        return _api_call_with_retry(_call)


# ============================================================
# デモプロバイダー
# ============================================================

class DemoProvider(ScoringProvider):
    """APIキーなしでUIを確認するためのデモプロバイダー"""

    @property
    def name(self) -> str:
        return "デモモード"

    def score_student(
        self,
        images: list[Image.Image],
        rubric: Rubric,
        reference_students: list[StudentResult] | None = None,
    ) -> dict:
        return generate_demo_scores(rubric)

    def score_question(
        self,
        images: list[Image.Image],
        question: Question,
        rubric_title: str,
        extract_student_name: bool = False,
        reference_students_info: list[dict] | None = None,
        notes: str = "",
    ) -> dict:
        return generate_demo_question_score(question, extract_student_name)

    def ocr_student(self, images, rubric):
        return generate_demo_ocr(rubric)

    def grade_question_batch(self, question, rubric_title,
                              students_answers, reference_info=None, notes=""):
        return generate_demo_horizontal_scores(question, students_answers)


def generate_demo_scores(rubric: Rubric) -> dict:
    """デモ用のランダムな仮採点結果を生成する"""
    demo_names = ["山田太郎", "佐藤花子", "鈴木一郎", "田中美咲", "高橋健太"]
    scores = []

    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                is_correct = random.random() > 0.3
                score_val = sq.points if is_correct else 0
                confidence = random.choice(["high", "high", "medium", "low"])
                scores.append({
                    "question_id": sq.id,
                    "score": score_val,
                    "max_points": sq.points,
                    "transcribed_text": sq.answer if is_correct else f"[デモ] {sq.answer[:1]}...",
                    "comment": "正答" if is_correct else "誤答または判読不明",
                    "confidence": confidence,
                    "needs_review": confidence == "low",
                })
        else:
            ratio = random.choice([0.0, 0.25, 0.5, 0.75, 1.0])
            score_val = round(q.max_points * ratio, 1)
            confidence = random.choice(["high", "medium", "medium", "low"])
            scores.append({
                "question_id": str(q.id),
                "score": score_val,
                "max_points": q.max_points,
                "transcribed_text": f"[デモ] 記述解答サンプル（{q.description}）",
                "comment": _demo_comment(ratio),
                "confidence": confidence,
                "needs_review": confidence == "low" or 0.25 <= ratio <= 0.75,
            })

    return {
        "student_name": random.choice(demo_names),
        "scores": scores,
        "overall_comment": "[デモモード] これはAPIキー未設定時のサンプル結果です。",
    }


def generate_demo_question_score(question: Question, include_name: bool = False) -> dict:
    """デモ用の1問分のランダムな仮採点結果を生成する"""
    demo_names = ["山田太郎", "佐藤花子", "鈴木一郎", "田中美咲", "高橋健太"]
    result: dict = {}

    if include_name:
        result["student_name"] = random.choice(demo_names)

    if question.sub_questions:
        scores = []
        for sq in question.sub_questions:
            is_correct = random.random() > 0.3
            score_val = sq.points if is_correct else 0
            confidence = random.choice(["high", "high", "medium", "low"])
            scores.append({
                "question_id": sq.id,
                "score": score_val,
                "max_points": sq.points,
                "transcribed_text": sq.answer if is_correct else f"[デモ] {sq.answer[:1]}...",
                "comment": "正答" if is_correct else "誤答または判読不明",
                "confidence": confidence,
                "needs_review": confidence == "low",
            })
        result["scores"] = scores
    else:
        ratio = random.choice([0.0, 0.25, 0.5, 0.75, 1.0])
        score_val = round(question.max_points * ratio, 1)
        confidence = random.choice(["high", "medium", "medium", "low"])
        result.update({
            "question_id": str(question.id),
            "score": score_val,
            "max_points": question.max_points,
            "transcribed_text": f"[デモ] 記述解答サンプル（{question.description[:20]}）",
            "comment": _demo_comment(ratio),
            "confidence": confidence,
            "needs_review": confidence == "low" or 0.25 <= ratio <= 0.75,
        })

    return result


def generate_demo_ocr(rubric: Rubric) -> dict:
    """デモ用のOCR結果"""
    demo_names = ["山田太郎", "佐藤花子", "鈴木一郎", "田中美咲", "高橋健太"]
    answers = []
    for q in rubric.questions:
        if q.sub_questions:
            for sq in q.sub_questions:
                answers.append({
                    "question_id": sq.id,
                    "transcribed_text": f"[デモ] {sq.answer}",
                    "confidence": "high",
                })
        else:
            answers.append({
                "question_id": str(q.id),
                "transcribed_text": f"[デモ] 記述解答サンプル（{q.description[:30]}）",
                "confidence": "medium",
            })
    return {
        "student_name": random.choice(demo_names),
        "answers": answers,
    }


def generate_demo_horizontal_scores(
    question: Question,
    students_answers: list[tuple[str, str, str]],
) -> dict:
    """デモ用の横断採点結果"""
    results = []
    for sid, sname, text in students_answers:
        if question.sub_questions:
            scores = []
            for sq in question.sub_questions:
                is_correct = random.random() > 0.3
                scores.append({
                    "question_id": sq.id,
                    "score": sq.points if is_correct else 0,
                    "max_points": sq.points,
                    "comment": "正答" if is_correct else "誤答",
                    "confidence": random.choice(["high", "medium"]),
                    "needs_review": not is_correct and random.random() > 0.5,
                })
            results.append({"student_id": sid, "scores": scores})
        else:
            ratio = random.choice([0.0, 0.25, 0.5, 0.75, 1.0])
            results.append({
                "student_id": sid,
                "question_id": str(question.id),
                "score": round(question.max_points * ratio, 1),
                "max_points": question.max_points,
                "comment": _demo_comment(ratio),
                "confidence": random.choice(["high", "medium", "low"]),
                "needs_review": 0.25 <= ratio <= 0.75,
            })
    return {"results": results}


def _demo_comment(ratio: float) -> str:
    if ratio >= 1.0:
        return "模範解答に近い内容"
    elif ratio >= 0.75:
        return "概ね正しいが、一部不足あり"
    elif ratio >= 0.5:
        return "要点の一部を捉えているが不十分"
    elif ratio >= 0.25:
        return "部分的に関連する記述あり"
    else:
        return "解答なしまたは的外れ"

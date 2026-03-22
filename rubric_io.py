"""ルーブリックの入出力と変換ユーティリティ。"""

from __future__ import annotations

import yaml

from models import GradingOptions, Question, Rubric, SubQuestion


def rubric_from_dict(data: dict) -> Rubric:
    """dict から Rubric オブジェクトを生成する。"""
    exam = data.get("exam_info", data)
    questions = []
    for qdata in data.get("questions", []):
        subs = []
        for sq in qdata.get("sub_questions", []):
            subs.append(SubQuestion(
                id=str(sq["id"]),
                text=sq.get("text", ""),
                answer=sq.get("answer", ""),
                points=sq.get("points", 0),
            ))
        questions.append(Question(
            id=qdata["id"],
            description=qdata.get("description", ""),
            question_type=qdata.get("question_type", qdata.get("type", "short_answer")),
            max_points=qdata.get("max_points", 0),
            scoring_criteria=qdata.get("scoring_criteria", ""),
            model_answer=qdata.get("model_answer", ""),
            sub_questions=subs,
        ))
    grading_options_data = data.get("grading_options")
    if isinstance(grading_options_data, dict):
        grading_options = GradingOptions(
            penalize_typos=grading_options_data.get("penalize_typos", False),
            penalize_grammar=grading_options_data.get("penalize_grammar", False),
            penalize_wrong_names=grading_options_data.get("penalize_wrong_names", False),
            penalize_hiragana=grading_options_data.get("penalize_hiragana", False),
            penalty_per_error=grading_options_data.get("penalty_per_error", 1.0),
            penalty_cap_ratio=grading_options_data.get("penalty_cap_ratio", 0.5),
        )
    else:
        grading_options = GradingOptions()

    return Rubric(
        title=exam.get("title", "無題の試験"),
        total_points=exam.get("total_points", 100),
        pages_per_student=exam.get("pages_per_student", 1),
        questions=questions,
        notes=data.get("notes", ""),
        grading_options=grading_options,
    )


def load_rubric_from_yaml(yaml_text: str) -> Rubric:
    """YAML文字列から採点基準を読み込む。"""
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("YAMLのトップレベルはオブジェクト形式である必要があります")
    return rubric_from_dict(data)


def rubric_to_yaml(rubric: Rubric) -> str:
    """RubricオブジェクトをYAML文字列に変換する。"""
    data = {
        "exam_info": {
            "title": rubric.title,
            "total_points": rubric.total_points,
            "pages_per_student": rubric.pages_per_student,
        },
        "notes": rubric.notes,
        "questions": [],
    }
    for q in rubric.questions:
        qd = {
            "id": q.id,
            "description": q.description,
            "type": q.question_type,
            "max_points": q.max_points,
        }
        if q.scoring_criteria:
            qd["scoring_criteria"] = q.scoring_criteria
        if q.model_answer:
            qd["model_answer"] = q.model_answer
        if q.sub_questions:
            qd["sub_questions"] = [
                {
                    "id": sq.id,
                    "text": sq.text,
                    "answer": sq.answer,
                    "points": sq.points,
                }
                for sq in q.sub_questions
            ]
        data["questions"].append(qd)
    # grading_options: いずれかの減点設定が有効な場合のみ出力
    go = rubric.grading_options
    if go.penalize_typos or go.penalize_grammar or go.penalize_wrong_names:
        data["grading_options"] = {
            "penalize_typos": go.penalize_typos,
            "penalize_grammar": go.penalize_grammar,
            "penalize_wrong_names": go.penalize_wrong_names,
            "penalty_per_error": go.penalty_per_error,
            "penalty_cap_ratio": go.penalty_cap_ratio,
        }
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def rubric_summary(rubric: Rubric) -> dict[str, int | str]:
    """APIやUIで使いやすいルーブリック要約を返す。"""
    return {
        "title": rubric.title,
        "question_count": len(rubric.questions),
        "total_points": rubric.total_points,
        "pages_per_student": rubric.pages_per_student,
    }

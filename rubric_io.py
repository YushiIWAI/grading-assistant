"""ルーブリックの入出力と変換ユーティリティ。"""

from __future__ import annotations

import yaml

from models import Question, Rubric, SubQuestion


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
    return Rubric(
        title=exam.get("title", "無題の試験"),
        total_points=exam.get("total_points", 100),
        pages_per_student=exam.get("pages_per_student", 1),
        questions=questions,
        notes=data.get("notes", ""),
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
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def rubric_summary(rubric: Rubric) -> dict[str, int | str]:
    """APIやUIで使いやすいルーブリック要約を返す。"""
    return {
        "title": rubric.title,
        "question_count": len(rubric.questions),
        "total_points": rubric.total_points,
        "pages_per_student": rubric.pages_per_student,
    }

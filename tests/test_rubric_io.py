"""rubric_io.py のユニットテスト"""

from rubric_io import (
    load_rubric_from_yaml,
    rubric_from_dict,
    rubric_summary,
    rubric_to_yaml,
)


class TestRubricIo:
    def test_round_trip_yaml(self, sample_rubric):
        yaml_text = rubric_to_yaml(sample_rubric)
        loaded = load_rubric_from_yaml(yaml_text)

        assert loaded.title == sample_rubric.title
        assert len(loaded.questions) == len(sample_rubric.questions)
        assert loaded.questions[0].sub_questions[0].answer == "むじゅん"

    def test_rubric_from_exam_info_dict(self):
        rubric = rubric_from_dict({
            "exam_info": {
                "title": "確認テスト",
                "total_points": 10,
                "pages_per_student": 2,
            },
            "questions": [
                {
                    "id": 1,
                    "description": "用語説明",
                    "type": "short_answer",
                    "max_points": 10,
                },
            ],
        })

        assert rubric.title == "確認テスト"
        assert rubric.pages_per_student == 2
        assert rubric.questions[0].description == "用語説明"

    def test_summary(self, sample_rubric):
        summary = rubric_summary(sample_rubric)
        assert summary["title"] == sample_rubric.title
        assert summary["question_count"] == 2
        assert summary["total_points"] == 25

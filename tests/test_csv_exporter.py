"""csv_exporter.py のユニットテスト"""

import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Question, QuestionScore, Rubric, ScoringSession, StudentResult,
)
from csv_exporter import (
    ExportConfig,
    _sanitize_csv_cell,
    export_results_csv,
    export_feedback_only_csv,
)


# ============================================================
# _sanitize_csv_cell
# ============================================================

class TestSanitizeCsvCell:
    def test_normal_text(self):
        assert _sanitize_csv_cell("普通のテキスト") == "普通のテキスト"

    def test_empty(self):
        assert _sanitize_csv_cell("") == ""

    def test_equals(self):
        assert _sanitize_csv_cell("=SUM(A1)") == "'=SUM(A1)"

    def test_plus(self):
        assert _sanitize_csv_cell("+cmd|'/c calc'|''!A0") == "'+cmd|'/c calc'|''!A0"

    def test_minus(self):
        assert _sanitize_csv_cell("-1+1") == "'-1+1"

    def test_at(self):
        assert _sanitize_csv_cell("@SUM(A1)") == "'@SUM(A1)"

    def test_tab(self):
        assert _sanitize_csv_cell("\t=cmd") == "'\t=cmd"

    def test_cr(self):
        assert _sanitize_csv_cell("\r=cmd") == "'\r=cmd"

    def test_safe_number(self):
        """数字はサニタイズ不要"""
        assert _sanitize_csv_cell("100") == "100"


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def simple_rubric():
    return Rubric(
        title="テスト試験",
        total_points=10,
        pages_per_student=1,
        questions=[
            Question(id=1, description="問1", max_points=5, question_type="descriptive"),
            Question(id=2, description="問2", max_points=5, question_type="short_answer"),
        ],
    )


@pytest.fixture
def simple_session():
    students = [
        StudentResult(
            student_id="1-1",
            student_name="1 1 山田太郎",
            status="ai_scored",
            total_score=8.0,
            total_max_points=10.0,
            question_scores=[
                QuestionScore(question_id="1", score=4.0, max_points=5.0,
                              comment="良い回答", feedback="よく書けています",
                              confidence="high"),
                QuestionScore(question_id="2", score=4.0, max_points=5.0,
                              comment="正解", feedback="正確です",
                              confidence="high"),
            ],
        ),
        StudentResult(
            student_id="1-2",
            student_name="1 2 佐藤花子",
            status="ai_scored",
            total_score=6.0,
            total_max_points=10.0,
            question_scores=[
                QuestionScore(question_id="1", score=3.0, max_points=5.0,
                              comment="部分的", feedback="もう少し具体的に",
                              confidence="medium"),
                QuestionScore(question_id="2", score=3.0, max_points=5.0,
                              comment="惜しい", feedback="",
                              confidence="medium"),
            ],
        ),
    ]
    return ScoringSession(students=students)


# ============================================================
# export_results_csv
# ============================================================

class TestExportResultsCsv:
    def test_basic_output(self, simple_session, simple_rubric):
        result = export_results_csv(simple_session, simple_rubric)
        # BOM check
        assert result.startswith("\ufeff")
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header + 2 students

    def test_includes_feedback_by_default(self, simple_session, simple_rubric):
        result = export_results_csv(simple_session, simple_rubric)
        assert "フィードバック" in result
        assert "よく書けています" in result

    def test_no_feedback(self, simple_session, simple_rubric):
        config = ExportConfig(include_feedback=False)
        result = export_results_csv(simple_session, simple_rubric, config=config)
        assert "フィードバック" not in result

    def test_combined_feedback(self, simple_session, simple_rubric):
        config = ExportConfig(include_feedback=True, combine_feedback=True)
        result = export_results_csv(simple_session, simple_rubric, config=config)
        assert "フィードバック（全問）" in result

    def test_empty_feedback_handled(self, simple_session, simple_rubric):
        """feedbackが空文字列でもクラッシュしない"""
        result = export_results_csv(simple_session, simple_rubric)
        # 佐藤花子の問2はfeedback=""
        reader = csv.reader(io.StringIO(result.lstrip("\ufeff")))
        rows = list(reader)
        # Should not crash
        assert len(rows) == 3

    def test_formula_injection_sanitized(self, simple_rubric):
        """=で始まるfeedbackがサニタイズされる（先頭に ' が付く）"""
        students = [
            StudentResult(
                student_id="1-1",
                student_name="山田",
                status="ai_scored",
                total_score=5.0,
                total_max_points=10.0,
                question_scores=[
                    QuestionScore(question_id="1", score=3.0, max_points=5.0,
                                  feedback="=CMD()"),
                    QuestionScore(question_id="2", score=2.0, max_points=5.0,
                                  feedback="safe"),
                ],
            ),
        ]
        session = ScoringSession(students=students)
        result = export_results_csv(session, simple_rubric)
        # サニタイズ後は "'=CMD()" になる（先頭に ' が付く）
        assert "'=CMD()" in result

    def test_zero_students(self, simple_rubric):
        session = ScoringSession(students=[])
        result = export_results_csv(session, simple_rubric)
        lines = result.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_student_name_parsing(self, simple_session, simple_rubric):
        """student_name "1 1 山田太郎" → 氏名列は "山田太郎" """
        result = export_results_csv(simple_session, simple_rubric)
        assert "山田太郎" in result


# ============================================================
# export_feedback_only_csv
# ============================================================

class TestExportFeedbackOnlyCsv:
    def test_output(self, simple_session, simple_rubric):
        result = export_feedback_only_csv(simple_session, simple_rubric)
        assert "フィードバック（全問）" in result
        assert "よく書けています" in result

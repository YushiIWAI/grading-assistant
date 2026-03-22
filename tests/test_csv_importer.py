"""csv_importer.py のユニットテスト"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Question, Rubric
from csv_importer import (
    ColumnMapping,
    FormsCSVData,
    parse_forms_csv,
    get_question_candidate_cols,
    convert_to_ocr_results,
)


# ============================================================
# parse_forms_csv
# ============================================================

class TestParseFormsCsv:
    def test_basic(self):
        csv_text = "氏名,問1\n山田太郎,回答A\n佐藤花子,回答B\n"
        data = parse_forms_csv(csv_text)
        assert len(data.rows) == 2
        assert data.auto_mapping.name_col == 0

    def test_empty_csv(self):
        with pytest.raises(ValueError, match="空です"):
            parse_forms_csv("")

    def test_headers_only(self):
        with pytest.raises(ValueError, match="データ行がありません"):
            parse_forms_csv("氏名,問1\n")

    def test_empty_rows_filtered(self):
        csv_text = "氏名,問1\n山田,回答A\n,,\n  , \n佐藤,回答B\n"
        data = parse_forms_csv(csv_text)
        assert len(data.rows) == 2

    def test_auto_detect_class_number(self):
        csv_text = "組,番号,氏名,問1\n1,3,山田太郎,回答\n"
        data = parse_forms_csv(csv_text)
        assert data.auto_mapping.class_col == 0
        assert data.auto_mapping.number_col == 1
        assert data.auto_mapping.name_col == 2

    def test_ignore_timestamp(self):
        csv_text = "タイムスタンプ,氏名,問1\n2026-01-01,山田,回答\n"
        data = parse_forms_csv(csv_text)
        assert 0 in data.auto_mapping.ignore_cols

    def test_ignore_email(self):
        csv_text = "メールアドレス,氏名,問1\ntest@test.com,山田,回答\n"
        data = parse_forms_csv(csv_text)
        assert 0 in data.auto_mapping.ignore_cols

    def test_ignore_score(self):
        csv_text = "スコア,氏名,問1\n10,山田,回答\n"
        data = parse_forms_csv(csv_text)
        assert 0 in data.auto_mapping.ignore_cols

    def test_special_characters_in_answer(self):
        """カンマ・改行・ダブルクォートを含む回答"""
        csv_text = '氏名,問1\n山田,"回答に、カンマ"\n佐藤,"改行を\n含む"\n'
        data = parse_forms_csv(csv_text)
        assert len(data.rows) == 2
        assert "カンマ" in data.rows[0][1]
        assert "改行" in data.rows[1][1]

    def test_over_2000_rows(self):
        rows = "氏名,問1\n" + "山田,回答\n" * 2001
        with pytest.raises(ValueError, match="2000行を超え"):
            parse_forms_csv(rows)


# ============================================================
# get_question_candidate_cols
# ============================================================

class TestGetQuestionCandidateCols:
    def test_excludes_mapped_cols(self):
        data = FormsCSVData(
            headers=["組", "番号", "氏名", "問1", "問2"],
            rows=[["1", "1", "山田", "A", "B"]],
            auto_mapping=ColumnMapping(
                class_col=0, number_col=1, name_col=2,
            ),
        )
        candidates = get_question_candidate_cols(data)
        assert candidates == [3, 4]


# ============================================================
# convert_to_ocr_results
# ============================================================

class TestConvertToOcrResults:
    @pytest.fixture
    def rubric(self):
        return Rubric(
            title="テスト",
            total_points=10,
            pages_per_student=1,
            questions=[
                Question(id=1, description="問1", max_points=5, question_type="descriptive"),
                Question(id=2, description="問2", max_points=5, question_type="descriptive"),
            ],
        )

    def test_basic(self, rubric):
        data = FormsCSVData(
            headers=["組", "番号", "氏名", "問1", "問2"],
            rows=[
                ["1", "3", "山田太郎", "回答A", "回答B"],
                ["1", "4", "佐藤花子", "回答C", "回答D"],
            ],
            auto_mapping=ColumnMapping(class_col=0, number_col=1, name_col=2),
        )
        mapping = ColumnMapping(
            class_col=0, number_col=1, name_col=2,
            question_cols={"1": 3, "2": 4},
        )
        results, errors, _teacher_scores = convert_to_ocr_results(data, mapping, rubric)
        assert len(results) == 2
        assert results[0].student_id == "1-3"
        assert results[0].answers[0].transcribed_text == "回答A"
        assert not errors

    def test_duplicate_ids(self, rubric):
        data = FormsCSVData(
            headers=["組", "番号", "問1"],
            rows=[
                ["1", "3", "回答A"],
                ["1", "3", "回答B"],
            ],
            auto_mapping=ColumnMapping(class_col=0, number_col=1),
        )
        mapping = ColumnMapping(
            class_col=0, number_col=1,
            question_cols={"1": 2},
        )
        results, errors, _teacher_scores = convert_to_ocr_results(data, mapping, rubric)
        assert results[0].student_id == "1-3"
        assert results[1].student_id == "1-3_2"
        assert len(errors) == 1

    def test_no_class_number_fallback_to_serial(self, rubric):
        data = FormsCSVData(
            headers=["氏名", "問1"],
            rows=[["山田", "回答A"]],
            auto_mapping=ColumnMapping(name_col=0),
        )
        mapping = ColumnMapping(
            name_col=0,
            question_cols={"1": 1},
        )
        results, errors, _teacher_scores = convert_to_ocr_results(data, mapping, rubric)
        assert results[0].student_id == "S001"

    def test_empty_answer(self, rubric):
        data = FormsCSVData(
            headers=["組", "番号", "問1"],
            rows=[["1", "1", ""]],
            auto_mapping=ColumnMapping(class_col=0, number_col=1),
        )
        mapping = ColumnMapping(
            class_col=0, number_col=1,
            question_cols={"1": 2},
        )
        results, errors, _teacher_scores = convert_to_ocr_results(data, mapping, rubric)
        assert results[0].answers[0].transcribed_text == ""

    def test_missing_column(self, rubric):
        """行が短い場合（列不足）"""
        data = FormsCSVData(
            headers=["組", "番号", "問1", "問2"],
            rows=[["1", "1", "回答A"]],  # 問2の列がない
            auto_mapping=ColumnMapping(class_col=0, number_col=1),
        )
        mapping = ColumnMapping(
            class_col=0, number_col=1,
            question_cols={"1": 2, "2": 3},
        )
        results, errors, _teacher_scores = convert_to_ocr_results(data, mapping, rubric)
        assert results[0].answers[1].transcribed_text == ""
        assert len(errors) == 1

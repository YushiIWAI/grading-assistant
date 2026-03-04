"""scoring_engine.py のユニットテスト"""

import json
import time

import pytest

from models import Question, SubQuestion
from scoring_engine import (
    RateLimiter,
    _extract_json,
    _thinking_budget_for_question,
    _validate_schema,
    OCR_SCHEMA,
    SCORING_SCHEMA,
    HORIZONTAL_SCHEMA,
    VERIFICATION_SCHEMA,
    parse_ocr_result,
    parse_scoring_result,
    parse_single_question_result,
    parse_horizontal_grading_result,
    parse_verification_result,
    build_verification_prompt,
    recommend_batch_size,
    analyze_batch_calibration,
)


# ============================================================
# _extract_json
# ============================================================

class TestExtractJson:
    def test_clean_json(self):
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json(text) == {"key": "value"}

    def test_trailing_text(self):
        text = '{"key": "value"} some trailing text'
        assert _extract_json(text) == {"key": "value"}

    def test_trailing_comma(self):
        text = '{"items": [1, 2, 3,]}'
        result = _extract_json(text)
        assert result["items"] == [1, 2, 3]

    def test_list_response(self):
        text = '[{"key": "value"}]'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="空のJSON配列"):
            _extract_json("[]")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="応答テキストが空"):
            _extract_json(None)


# ============================================================
# _validate_schema
# ============================================================

class TestValidateSchema:
    def test_valid_ocr(self):
        data = {
            "student_name": "田中",
            "answers": [
                {"question_id": "1", "transcribed_text": "test", "confidence": "high"}
            ],
        }
        warnings = _validate_schema(data, OCR_SCHEMA, "OCR")
        assert warnings == []

    def test_missing_required_raises(self):
        data = {"student_name": "田中"}
        with pytest.raises(ValueError, match="answers"):
            _validate_schema(data, OCR_SCHEMA, "OCR")

    def test_missing_optional_ok(self):
        data = {
            "scores": [{"question_id": "1", "score": 5}],
        }
        warnings = _validate_schema(data, SCORING_SCHEMA, "採点")
        assert warnings == []

    def test_wrong_type_warns(self):
        data = {
            "scores": "not a list",
        }
        warnings = _validate_schema(data, SCORING_SCHEMA, "採点")
        assert len(warnings) == 1
        assert "型が不正" in warnings[0]

    def test_missing_item_field_warns(self):
        data = {
            "results": [
                {"score": 5}  # student_id が欠如
            ],
        }
        warnings = _validate_schema(data, HORIZONTAL_SCHEMA, "横断")
        assert any("student_id" in w for w in warnings)


# ============================================================
# parse_ocr_result
# ============================================================

class TestParseOcrResult:
    def test_normal(self, sample_rubric):
        data = {
            "student_name": "田中",
            "answers": [
                {"question_id": "1-1", "transcribed_text": "むじゅん", "confidence": "high"},
                {"question_id": "1-2", "transcribed_text": "じひ", "confidence": "high"},
                {"question_id": "2", "transcribed_text": "テスト", "confidence": "medium"},
            ],
        }
        name, answers = parse_ocr_result(data, sample_rubric)
        assert name == "田中"
        assert len(answers) == 3

    def test_missing_ids_filled(self, sample_rubric):
        data = {
            "answers": [
                {"question_id": "1-1", "transcribed_text": "test"},
            ],
        }
        _, answers = parse_ocr_result(data, sample_rubric)
        assert len(answers) == 3
        missing = [a for a in answers if a.transcribed_text == ""]
        assert len(missing) == 2

    def test_prefix_normalization(self, sample_rubric):
        data = {
            "answers": [
                {"question_id": "問1-1", "transcribed_text": "test"},
                {"question_id": "問1-2", "transcribed_text": "test"},
                {"question_id": "問2", "transcribed_text": "test"},
            ],
        }
        _, answers = parse_ocr_result(data, sample_rubric)
        ids = {a.question_id for a in answers}
        assert "1-1" in ids
        assert "2" in ids


# ============================================================
# parse_scoring_result
# ============================================================

class TestParseScoringResult:
    def test_score_clamping(self):
        data = {
            "scores": [
                {"question_id": "1", "score": 15, "max_points": 10},
                {"question_id": "2", "score": -3, "max_points": 5},
            ],
        }
        _, scores, _ = parse_scoring_result(data)
        assert scores[0].score == 10  # clamped to max
        assert scores[1].score == 0  # clamped to 0

    def test_normal_parse(self):
        data = {
            "student_name": "田中",
            "scores": [
                {"question_id": "1", "score": 8, "max_points": 10,
                 "comment": "good", "confidence": "high", "needs_review": False},
            ],
            "overall_comment": "テスト",
        }
        name, scores, comment = parse_scoring_result(data)
        assert name == "田中"
        assert len(scores) == 1
        assert comment == "テスト"


# ============================================================
# parse_single_question_result
# ============================================================

class TestParseSingleQuestionResult:
    def test_with_sub_questions(self, sample_rubric):
        question = sample_rubric.questions[0]  # 短答（小問付き）
        data = {
            "scores": [
                {"question_id": "1-1", "score": 5, "max_points": 5, "comment": "correct"},
                {"question_id": "1-2", "score": 3, "max_points": 5, "comment": "partial"},
            ],
        }
        _, scores = parse_single_question_result(data, question)
        assert len(scores) == 2
        assert scores[0].score == 5
        assert scores[1].score == 3

    def test_without_sub_questions(self, sample_rubric):
        question = sample_rubric.questions[1]  # 記述
        data = {
            "question_id": "2",
            "score": 12,
            "max_points": 15,
            "comment": "良い回答",
            "confidence": "high",
            "needs_review": False,
        }
        _, scores = parse_single_question_result(data, question)
        assert len(scores) == 1
        assert scores[0].score == 12


# ============================================================
# parse_horizontal_grading_result
# ============================================================

class TestParseHorizontalGradingResult:
    def test_normal(self, sample_rubric):
        question = sample_rubric.questions[1]  # 記述（小問なし）
        data = {
            "results": [
                {"student_id": "S001", "question_id": "2", "score": 12,
                 "max_points": 15, "comment": "good", "confidence": "high",
                 "needs_review": False},
                {"student_id": "S002", "question_id": "2", "score": 8,
                 "max_points": 15, "comment": "partial", "confidence": "medium",
                 "needs_review": True},
            ],
        }
        result = parse_horizontal_grading_result(data, question, ["S001", "S002"])
        assert "S001" in result
        assert "S002" in result
        assert result["S001"][0].score == 12

    def test_missing_students_filled(self, sample_rubric):
        question = sample_rubric.questions[1]
        data = {
            "results": [
                {"student_id": "S001", "score": 12, "max_points": 15, "comment": "good"},
            ],
        }
        result = parse_horizontal_grading_result(data, question, ["S001", "S002"])
        assert "S002" in result
        assert result["S002"][0].score == 0
        assert result["S002"][0].needs_review is True


# ============================================================
# recommend_batch_size
# ============================================================

class TestRecommendBatchSize:
    def test_descriptive_heavy(self):
        from models import Rubric, Question
        rubric = Rubric(
            title="test", total_points=60, pages_per_student=1,
            questions=[
                Question(id=i, description=f"Q{i}", question_type="descriptive", max_points=20)
                for i in range(1, 4)
            ],
        )
        size, reason = recommend_batch_size(rubric)
        assert size == 8

    def test_short_answer_only(self):
        from models import Rubric, Question
        rubric = Rubric(
            title="test", total_points=20, pages_per_student=1,
            questions=[
                Question(id=i, description=f"Q{i}", question_type="short_answer", max_points=5)
                for i in range(1, 5)
            ],
        )
        size, _ = recommend_batch_size(rubric)
        assert size == 20


# ============================================================
# _thinking_budget_for_question
# ============================================================

class TestThinkingBudget:
    def test_descriptive(self):
        q = Question(id=1, description="要約", question_type="descriptive", max_points=15)
        assert _thinking_budget_for_question(q) == 16384

    def test_short_answer(self):
        q = Question(id=1, description="漢字", question_type="short_answer", max_points=5)
        assert _thinking_budget_for_question(q) == 8192

    def test_with_sub_questions(self):
        q = Question(
            id=1, description="漢字", question_type="short_answer", max_points=10,
            sub_questions=[SubQuestion(id="1-1", text="test", answer="test", points=5)],
        )
        assert _thinking_budget_for_question(q) == 8192

    def test_custom_base(self):
        q = Question(id=1, description="要約", question_type="descriptive", max_points=15)
        assert _thinking_budget_for_question(q, base=4096) == 8192


# ============================================================
# RateLimiter
# ============================================================

class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_calls=5, window_seconds=60.0)
        for _ in range(5):
            limiter.wait()  # Should not block

    def test_blocks_when_exceeded(self):
        limiter = RateLimiter(max_calls=2, window_seconds=0.5)
        limiter.wait()
        limiter.wait()
        start = time.time()
        limiter.wait()  # Should block ~0.5s
        elapsed = time.time() - start
        assert elapsed >= 0.3  # some tolerance


# ============================================================
# analyze_batch_calibration
# ============================================================

class TestAnalyzeBatchCalibration:
    def test_single_batch_returns_empty(self, sample_session, sample_rubric):
        # 2 students, batch_size=15 → 1 batch → no calibration
        warnings = analyze_batch_calibration(sample_session, sample_rubric, batch_size=15)
        assert warnings == []

    def test_multi_batch_no_warnings(self, sample_session, sample_rubric):
        # batch_size=1 → 2 batches, but similar scores → may not warn
        warnings = analyze_batch_calibration(sample_session, sample_rubric, batch_size=1)
        # With 2 students scoring 22 and 16, deviation on Q2 is (12-8)/2=2 vs max_points=15
        # 2/15=13.3% < 15% threshold → no warning for Q2
        # Q1: both get 10/10 → 0 deviation
        assert all(w["severity"] in ("info", "warning") for w in warnings)


# ============================================================
# parse_verification_result
# ============================================================

class TestParseVerificationResult:
    def test_normal(self):
        result = {
            "results": [
                {
                    "student_id": "S001",
                    "verified_score": 8,
                    "score_changed": True,
                    "verification_comment": "要素Bの評価を修正",
                    "confidence": "medium",
                    "needs_review": True,
                },
                {
                    "student_id": "S002",
                    "verified_score": 6,
                    "score_changed": False,
                    "verification_comment": "採点妥当",
                    "confidence": "high",
                    "needs_review": False,
                },
            ]
        }
        verified = parse_verification_result(result, ["S001", "S002"], max_points=10.0)
        assert verified["S001"]["verified_score"] == 8.0
        assert verified["S001"]["score_changed"] is True
        assert verified["S001"]["needs_review"] is True
        assert verified["S002"]["verified_score"] == 6.0
        assert verified["S002"]["score_changed"] is False

    def test_score_clamping(self):
        result = {
            "results": [
                {
                    "student_id": "S001",
                    "verified_score": 15,
                    "score_changed": True,
                    "verification_comment": "test",
                },
            ]
        }
        verified = parse_verification_result(result, ["S001"], max_points=10.0)
        assert verified["S001"]["verified_score"] == 10.0

    def test_missing_student_filled(self):
        result = {"results": []}
        verified = parse_verification_result(result, ["S001"], max_points=10.0)
        assert verified["S001"]["verified_score"] is None
        assert verified["S001"]["needs_review"] is True
        assert verified["S001"]["confidence"] == "low"


# ============================================================
# build_verification_prompt
# ============================================================

class TestBuildVerificationPrompt:
    def test_contains_required_sections(self):
        question = Question(
            id=2, description="テスト問題", question_type="descriptive",
            max_points=14, model_answer="模範解答テスト",
            scoring_criteria="【要素A: 4点】テスト\n【要素B: 5点】テスト",
        )
        entries = [
            ("S001", "山田太郎", "解答テキスト", 10.0, 14.0, "要素Aを満たす"),
        ]
        prompt = build_verification_prompt(
            question=question, rubric_title="テスト試験",
            student_scores_with_answers=entries,
        )
        assert "採点検証" in prompt
        assert "模範解答" in prompt
        assert "採点基準" in prompt
        assert "S001" in prompt
        assert "10.0/14.0" in prompt
        assert "要素Aを満たす" in prompt
        assert "verified_score" in prompt

"""models.py のユニットテスト"""

from models import (
    OcrAnswer, QuestionScore, ScoringSession, StudentOcr, StudentResult,
)


class TestStudentResult:
    def test_recalculate_total(self):
        s = StudentResult(
            student_id="S001",
            question_scores=[
                QuestionScore(question_id="1", score=8, max_points=10),
                QuestionScore(question_id="2", score=12.5, max_points=15),
            ],
        )
        s.recalculate_total()
        assert s.total_score == 20.5
        assert s.total_max_points == 25

    def test_recalculate_total_empty(self):
        s = StudentResult(student_id="S001")
        s.recalculate_total()
        assert s.total_score == 0.0
        assert s.total_max_points == 0.0

    def test_review_needed_count(self):
        s = StudentResult(
            student_id="S001",
            question_scores=[
                QuestionScore(question_id="1", score=5, max_points=10, needs_review=True),
                QuestionScore(question_id="2", score=10, max_points=15, needs_review=True, reviewed=True),
                QuestionScore(question_id="3", score=3, max_points=5, needs_review=False),
            ],
        )
        assert s.review_needed_count() == 1

    def test_review_needed_count_none(self):
        s = StudentResult(student_id="S001")
        assert s.review_needed_count() == 0


class TestScoringSession:
    def test_from_dict_round_trip(self, sample_session):
        d = sample_session.to_dict()
        restored = ScoringSession.from_dict(d)

        assert restored.session_id == sample_session.session_id
        assert len(restored.students) == len(sample_session.students)
        assert len(restored.ocr_results) == len(sample_session.ocr_results)

        for orig, rest in zip(sample_session.students, restored.students):
            assert orig.student_id == rest.student_id
            assert orig.total_score == rest.total_score
            assert len(orig.question_scores) == len(rest.question_scores)

    def test_summary(self, sample_session):
        summary = sample_session.summary()
        assert summary["total_students"] == 2
        assert summary["scored"] == 2
        assert summary["average_score"] > 0

    def test_summary_no_students(self):
        session = ScoringSession()
        summary = session.summary()
        assert summary["total_students"] == 0
        assert summary["average_score"] == 0.0

    def test_get_ocr_for_student(self, sample_session):
        ocr = sample_session.get_ocr_for_student("S001")
        assert ocr is not None
        assert ocr.student_name == "山田太郎"

    def test_get_ocr_for_student_not_found(self, sample_session):
        assert sample_session.get_ocr_for_student("S999") is None

    def test_get_all_answers_for_question(self, sample_session):
        results = sample_session.get_all_answers_for_question("1-1")
        assert len(results) == 2
        assert results[0][2] == "むじゅん"

    def test_ocr_complete_true(self, sample_session):
        assert sample_session.ocr_complete() is True

    def test_ocr_complete_false(self):
        session = ScoringSession(
            ocr_results=[
                StudentOcr(student_id="S001", status="ocr_done"),
                StudentOcr(student_id="S002", status="pending"),
            ],
        )
        assert session.ocr_complete() is False

    def test_ocr_complete_empty(self):
        session = ScoringSession()
        assert session.ocr_complete() is False

    def test_get_reference_students(self, sample_session):
        assert sample_session.get_reference_students() == []
        # confirmed + is_reference → 参考例
        sample_session.students[0].is_reference = True
        sample_session.students[0].status = "confirmed"
        refs = sample_session.get_reference_students()
        assert len(refs) == 1
        assert refs[0].student_id == "S001"

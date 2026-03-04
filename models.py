"""採点支援アプリのデータモデル定義"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class SubQuestion:
    """小問（漢字の読みなど個別の小問）"""
    id: str
    text: str  # 問題文や対象の語句
    answer: str  # 模範解答
    points: int  # 配点


@dataclass
class Question:
    """設問"""
    id: int
    description: str  # 問題の説明
    question_type: str  # "short_answer", "descriptive", "selection"
    max_points: int
    scoring_criteria: str = ""  # 採点基準の詳細
    model_answer: str = ""  # 模範解答（記述問題用）
    sub_questions: list[SubQuestion] = field(default_factory=list)


@dataclass
class Rubric:
    """採点基準（試験全体）"""
    title: str  # 試験名
    total_points: int  # 満点
    pages_per_student: int  # 学生1人あたりのページ数
    questions: list[Question] = field(default_factory=list)
    notes: str = ""  # 採点上の注意事項


@dataclass
class OcrAnswer:
    """1問分のOCR読み取り結果"""
    question_id: str
    transcribed_text: str
    confidence: str = "medium"
    manually_corrected: bool = False


@dataclass
class StudentOcr:
    """学生1人分のOCR結果"""
    student_id: str
    student_name: str = ""
    page_numbers: list[int] = field(default_factory=list)
    answers: list[OcrAnswer] = field(default_factory=list)
    status: str = "pending"  # "pending" | "ocr_done" | "reviewed"
    ocr_error: str = ""


@dataclass
class QuestionScore:
    """1問ごとの採点結果"""
    question_id: str  # 設問ID ("1" or "1-1" for sub-questions)
    score: float  # 得点
    max_points: float  # 配点
    transcribed_text: str = ""  # AIが読み取ったテキスト
    comment: str = ""  # 採点コメント
    confidence: str = "medium"  # "high", "medium", "low"
    needs_review: bool = False  # 要確認フラグ
    reviewed: bool = False  # 教員確認済みフラグ
    ai_score: float | None = None  # AI初期スコアのバックアップ（教員修正後に「戻す」用）


@dataclass
class StudentResult:
    """学生1人分の採点結果"""
    student_id: str  # 学生識別子
    student_name: str = ""  # 氏名（読み取れた場合）
    page_numbers: list[int] = field(default_factory=list)  # PDF内のページ番号
    question_scores: list[QuestionScore] = field(default_factory=list)
    total_score: float = 0.0
    total_max_points: float = 0.0
    status: str = "pending"  # "pending", "ai_scored", "confirmed"
    reviewer_notes: str = ""  # 教員のメモ
    ai_overall_comment: str = ""  # AIの総合コメント
    is_reference: bool = False  # 教員の採点を参考例としてAIに提供するか

    def recalculate_total(self):
        """小問の得点から合計を再計算"""
        self.total_score = sum(q.score for q in self.question_scores)
        self.total_max_points = sum(q.max_points for q in self.question_scores)

    def review_needed_count(self) -> int:
        """要確認の設問数"""
        return sum(1 for q in self.question_scores if q.needs_review and not q.reviewed)


@dataclass
class ScoringSession:
    """採点セッション（1回の採点作業全体）"""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    rubric_title: str = ""
    pdf_filename: str = ""
    pages_per_student: int = 1
    students: list[StudentResult] = field(default_factory=list)
    ocr_results: list[StudentOcr] = field(default_factory=list)
    grading_mode: str = "legacy"  # "legacy" | "horizontal"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ScoringSession:
        students = []
        for s in data.get("students", []):
            scores = [QuestionScore(**q) for q in s.pop("question_scores", [])]
            student = StudentResult(**s)
            student.question_scores = scores
            students.append(student)
        data["students"] = students

        ocr_results = []
        for o in data.get("ocr_results", []):
            answers = [OcrAnswer(**a) for a in o.pop("answers", [])]
            ocr_obj = StudentOcr(**o)
            ocr_obj.answers = answers
            ocr_results.append(ocr_obj)
        data["ocr_results"] = ocr_results

        return cls(**data)

    def get_reference_students(self) -> list[StudentResult]:
        """参考例としてマークされた学生を返す"""
        return [s for s in self.students if s.is_reference and s.status in ("reviewed", "confirmed")]

    def get_ocr_for_student(self, student_id: str) -> StudentOcr | None:
        """student_idに対応するOCR結果を返す"""
        for o in self.ocr_results:
            if o.student_id == student_id:
                return o
        return None

    def get_all_answers_for_question(self, question_id: str) -> list[tuple[str, str, str]]:
        """指定問のOCR結果を全学生分返す。Returns: list of (student_id, student_name, text)"""
        results = []
        for ocr in self.ocr_results:
            if ocr.status in ("ocr_done", "reviewed"):
                for ans in ocr.answers:
                    if ans.question_id == question_id:
                        results.append((ocr.student_id, ocr.student_name, ans.transcribed_text))
        return results

    def ocr_complete(self) -> bool:
        """全学生のOCRが完了しているか"""
        return bool(self.ocr_results) and all(
            o.status in ("ocr_done", "reviewed") for o in self.ocr_results
        )

    def summary(self) -> dict:
        """セッション全体のサマリー"""
        total = len(self.students)
        scored = sum(1 for s in self.students if s.status != "pending")
        reviewed = sum(1 for s in self.students if s.status in ("reviewed", "confirmed"))
        needs_review = sum(s.review_needed_count() for s in self.students)
        avg_score = 0.0
        if scored > 0:
            avg_score = sum(s.total_score for s in self.students if s.status != "pending") / scored
        return {
            "total_students": total,
            "scored": scored,
            "reviewed": reviewed,
            "needs_review_items": needs_review,
            "average_score": round(avg_score, 1),
        }

"""_resolve_student_id / _normalize_sid / _build_grading_options_prompt のユニットテスト"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import GradingOptions, Question
from scoring_engine import (
    _normalize_sid,
    _resolve_student_id,
    _build_grading_options_prompt,
    build_horizontal_grading_prompt,
)


# ============================================================
# _normalize_sid
# ============================================================

class TestNormalizeSid:
    def test_basic(self):
        assert _normalize_sid("1-1") == "1-1"

    def test_spaces_to_hyphens(self):
        assert _normalize_sid("1 1") == "1-1"

    def test_underscores_to_hyphens(self):
        assert _normalize_sid("1_1") == "1-1"

    def test_s_prefix_removed(self):
        assert _normalize_sid("S001") == "001"

    def test_s_prefix_case_insensitive(self):
        assert _normalize_sid("s001") == "001"

    def test_s_prefix_not_removed_when_part_of_name(self):
        # "s" followed by non-digit, non-separator is kept
        assert _normalize_sid("satou") == "satou"

    def test_fullwidth_paren_stripped(self):
        """AIが "1-12（1 12 渡辺陽菜）" のように返す場合、括弧以降を除去"""
        assert _normalize_sid("1-12（1 12 渡辺陽菜）") == "1-12"

    def test_halfwidth_paren_stripped(self):
        assert _normalize_sid("1-12(渡辺陽菜)") == "1-12"

    def test_paren_with_spaces(self):
        assert _normalize_sid("1 12 （渡辺）") == "1-12"

    def test_empty_string(self):
        assert _normalize_sid("") == ""

    def test_whitespace_stripped(self):
        assert _normalize_sid("  1-1  ") == "1-1"

    def test_multiple_spaces(self):
        assert _normalize_sid("1  1  山田") == "1-1-山田"

    def test_mixed_separators(self):
        assert _normalize_sid("1_1 山田") == "1-1-山田"

    def test_lowercase(self):
        assert _normalize_sid("ABC") == "abc"


# ============================================================
# _resolve_student_id
# ============================================================

class TestResolveStudentId:
    """IDマッチングの正確性をテストする。"""

    # --- 完全一致 ---

    def test_exact_match(self):
        assert _resolve_student_id("1-1", ["1-1", "1-2", "1-3"]) == "1-1"

    def test_exact_match_with_name(self):
        assert _resolve_student_id("1-1-山田太郎", ["1-1-山田太郎", "1-2"]) == "1-1-山田太郎"

    # --- 正規化後の完全一致 ---

    def test_space_to_hyphen(self):
        """AI が "1 1" を返した場合 → "1-1" にマッチ"""
        assert _resolve_student_id("1 1", ["1-1", "1-2"]) == "1-1"

    def test_underscore_to_hyphen(self):
        assert _resolve_student_id("1_1", ["1-1", "1-2"]) == "1-1"

    def test_s_prefix(self):
        """AI が "S001" を返した場合 → "001" にマッチ"""
        assert _resolve_student_id("S001", ["001", "002"]) == "001"

    # --- 先頭一致（一意） ---

    def test_prefix_match_raw_contains_extra(self):
        """AI が "1-1-山田太郎" を返した場合 → "1-1" にマッチ"""
        assert _resolve_student_id("1-1-山田太郎", ["1-1", "1-2"]) == "1-1"

    def test_prefix_match_expected_contains_extra(self):
        """AI が "1-1" を返した場合 → "1-1-山田太郎" にマッチ（一意）"""
        assert _resolve_student_id("1-1", ["1-1-山田太郎"]) == "1-1-山田太郎"

    # --- 先頭一致（曖昧 → None） ---

    def test_prefix_match_ambiguous(self):
        """AI が "1-1" を返したが "1-1-山田太郎" と "1-1-山田花子" の両方にマッチ → None"""
        result = _resolve_student_id("1-1", ["1-1-山田太郎", "1-1-山田花子"])
        assert result is None

    # --- 括弧付きID ---

    def test_fullwidth_paren_id(self):
        """AI が "1-12（1 12 渡辺陽菜）" を返す → "1-12" にマッチ"""
        assert _resolve_student_id("1-12（1 12 渡辺陽菜）", ["1-1", "1-12"]) == "1-12"

    def test_halfwidth_paren_id(self):
        assert _resolve_student_id("1-12(渡辺)", ["1-1", "1-12"]) == "1-12"

    # --- 部分一致（一意） ---

    def test_substring_unique(self):
        """AI が "山田" を返し、"山田太郎" だけが含む → マッチ"""
        assert _resolve_student_id("山田", ["1-1-佐藤", "1-2-山田太郎"]) == "1-2-山田太郎"

    # --- 部分一致（曖昧 → None） ---

    def test_substring_ambiguous_same_family_name(self):
        """AI が "山田" を返し、山田太郎と山田花子がいる → None"""
        result = _resolve_student_id("山田", ["1-1-山田太郎", "1-2-山田花子"])
        assert result is None

    # --- 空文字列 ---

    def test_empty_string(self):
        """空文字列は None を返す（全生徒にマッチしてはいけない）"""
        assert _resolve_student_id("", ["1-1", "1-2"]) is None

    def test_whitespace_only(self):
        """空白のみも None を返す"""
        assert _resolve_student_id("   ", ["1-1", "1-2"]) is None

    # --- マッチしない ---

    def test_no_match(self):
        assert _resolve_student_id("9-9", ["1-1", "1-2"]) is None

    # --- 1-1 vs 1-10 の衝突防止 ---

    def test_similar_ids_exact(self):
        """完全一致が優先される"""
        assert _resolve_student_id("1-1", ["1-1", "1-10", "1-100"]) == "1-1"

    def test_similar_ids_no_exact(self):
        """完全一致がなく、先頭一致もなければ部分一致。"1" は複数にマッチ → None"""
        result = _resolve_student_id("1", ["1-1", "1-2", "1-3"])
        assert result is None

    # --- AI が名前付きで返す ---

    def test_ai_returns_id_with_name(self):
        """AI が "1 1 山田太郎" を返す → 正規化で "1-1-山田太郎" → "1-1" に先頭一致"""
        assert _resolve_student_id("1 1 山田太郎", ["1-1", "1-2"]) == "1-1"

    # --- S prefix + exact match priority ---

    def test_s_prefix_exact_match_priority(self):
        """完全一致 "S001" が先にヒットする"""
        assert _resolve_student_id("S001", ["001", "S001"]) == "S001"


# ============================================================
# _build_grading_options_prompt
# ============================================================

class TestBuildGradingOptionsPrompt:
    def test_none(self):
        assert _build_grading_options_prompt(None) == ""

    def test_all_false(self):
        opts = GradingOptions()
        assert _build_grading_options_prompt(opts) == ""

    def test_typos_only(self):
        opts = GradingOptions(penalize_typos=True)
        result = _build_grading_options_prompt(opts)
        assert "誤字・脱字" in result
        assert "表記・文法の減点ルール" in result
        assert "内容の採点" in result

    def test_wrong_names_excludes_content_errors(self):
        """penalize_wrong_names は表記ミスに限定し、内容の誤りは除外する"""
        opts = GradingOptions(penalize_wrong_names=True)
        result = _build_grading_options_prompt(opts)
        assert "表記ミス" in result
        assert "内容として間違った" in result

    def test_penalty_values(self):
        opts = GradingOptions(
            penalize_typos=True,
            penalty_per_error=2.0,
            penalty_cap_ratio=0.3,
        )
        result = _build_grading_options_prompt(opts)
        assert "-2.0点" in result
        assert "30%" in result

    def test_all_true(self):
        opts = GradingOptions(
            penalize_typos=True,
            penalize_grammar=True,
            penalize_wrong_names=True,
        )
        result = _build_grading_options_prompt(opts)
        assert "誤字・脱字" in result
        assert "文法の誤り" in result
        assert "表記ミス" in result


# ============================================================
# build_horizontal_grading_prompt — grading_options 統合テスト
# ============================================================

class TestBuildHorizontalGradingPromptWithOptions:
    """grading_options がプロンプト末端まで正しく届くかのプラミングテスト"""

    @pytest.fixture
    def question(self):
        return Question(
            id=1,
            description="閏土の変化について述べよ",
            question_type="descriptive",
            max_points=5,
            scoring_criteria="変化の内容と理由を述べていること",
        )

    @pytest.fixture
    def students(self):
        return [
            ("1-1", "山田太郎", "閏土は大人になって変わってしまった"),
            ("1-2", "佐藤花子", "閏土は旧来の身分制度に縛られるようになった"),
        ]

    def test_no_options(self, question, students):
        """GradingOptions なしの場合、減点ルールがプロンプトに含まれない"""
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
        )
        assert "表記・文法の減点ルール" not in prompt

    def test_options_none(self, question, students):
        """grading_options=None も同様"""
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
            grading_options=None,
        )
        assert "表記・文法の減点ルール" not in prompt

    def test_options_all_false(self, question, students):
        """全フラグ False の場合も減点ルールなし"""
        opts = GradingOptions()
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
            grading_options=opts,
        )
        assert "表記・文法の減点ルール" not in prompt

    def test_typos_enabled(self, question, students):
        """penalize_typos=True の場合、誤字・脱字がプロンプトに含まれる"""
        opts = GradingOptions(penalize_typos=True)
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
            grading_options=opts,
        )
        assert "表記・文法の減点ルール" in prompt
        assert "誤字・脱字" in prompt
        assert "内容の採点" in prompt

    def test_all_options_enabled(self, question, students):
        """全フラグ True の場合、全減点対象がプロンプトに含まれる"""
        opts = GradingOptions(
            penalize_typos=True,
            penalize_grammar=True,
            penalize_wrong_names=True,
            penalty_per_error=2.0,
            penalty_cap_ratio=0.3,
        )
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
            grading_options=opts,
        )
        assert "誤字・脱字" in prompt
        assert "文法の誤り" in prompt
        assert "表記ミス" in prompt
        assert "-2.0点" in prompt
        assert "30%" in prompt

    def test_options_position_before_answers(self, question, students):
        """減点ルールが生徒解答よりも前に配置される"""
        opts = GradingOptions(penalize_typos=True)
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
            grading_options=opts,
        )
        options_pos = prompt.index("表記・文法の減点ルール")
        answers_pos = prompt.index("採点対象の解答一覧")
        assert options_pos < answers_pos

    def test_prompt_contains_feedback_field(self, question, students):
        """プロンプトのJSONスキーマに feedback フィールドが含まれる"""
        prompt = build_horizontal_grading_prompt(
            question=question,
            rubric_title="故郷テスト",
            students_answers=students,
        )
        assert '"feedback"' in prompt

"""Google Forms 回答CSVのインポート処理"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

from models import OcrAnswer, Rubric, StudentOcr

logger = logging.getLogger(__name__)

# 自動推定用キーワード
# _EXACT: ヘッダー全体が完全一致（strip後）で判定
# _CONTAINS: ヘッダーに部分一致で判定（誤検出が少ないもののみ）

_IGNORE_EXACT = [
    "タイムスタンプ", "timestamp", "送信日時",
    "スコア", "score", "点数", "合計", "合計点",
    "メールアドレス", "email",
]

_CLASS_EXACT = ["組", "クラス", "学級"]

_NUMBER_EXACT = ["番号", "出席番号"]

_NAME_EXACT = ["氏名", "名前", "生徒名", "名前（フルネーム）"]


@dataclass
class ColumnMapping:
    """CSVの各列が何を表すか"""
    class_col: int | None = None
    number_col: int | None = None
    name_col: int | None = None
    question_cols: dict[str, int] = field(default_factory=dict)
    ignore_cols: list[int] = field(default_factory=list)


@dataclass
class FormsCSVData:
    """パース済みCSVデータ"""
    headers: list[str]
    rows: list[list[str]]
    auto_mapping: ColumnMapping


def _matches_exact(header: str, keywords: list[str]) -> bool:
    """ヘッダー全体がキーワードリストのいずれかに完全一致するか（大文字小文字不問）"""
    h = header.strip().lower()
    return any(h == kw.lower() for kw in keywords)


def parse_forms_csv(csv_content: str) -> FormsCSVData:
    """Google Forms回答CSVをパースし、列の役割を自動推定する。

    Args:
        csv_content: CSVファイルの内容（文字列）

    Returns:
        FormsCSVData: ヘッダー、データ行、自動推定された列マッピング
    """
    reader = csv.reader(io.StringIO(csv_content))
    rows_raw = list(reader)

    if not rows_raw:
        raise ValueError("CSVファイルが空です")

    headers = rows_raw[0]
    data_rows = rows_raw[1:]

    # 空行を除外
    data_rows = [row for row in data_rows if any(cell.strip() for cell in row)]

    if not data_rows:
        raise ValueError("CSVにデータ行がありません")

    MAX_ROWS = 2000
    if len(data_rows) > MAX_ROWS:
        raise ValueError(f"データ行が{MAX_ROWS}行を超えています（{len(data_rows)}行）。ファイルを分割してください。")

    # 列の役割を自動推定
    mapping = ColumnMapping()

    for i, header in enumerate(headers):
        h = header.strip()
        if not h:
            mapping.ignore_cols.append(i)
        elif _matches_exact(h, _IGNORE_EXACT):
            mapping.ignore_cols.append(i)
        elif _matches_exact(h, _CLASS_EXACT):
            mapping.class_col = i
        elif _matches_exact(h, _NUMBER_EXACT):
            mapping.number_col = i
        elif _matches_exact(h, _NAME_EXACT):
            mapping.name_col = i
        # それ以外は設問候補（question_cols には入れない。UIでマッピングする）

    return FormsCSVData(
        headers=headers,
        rows=data_rows,
        auto_mapping=mapping,
    )


def get_question_candidate_cols(data: FormsCSVData) -> list[int]:
    """設問候補の列インデックスを返す（自動推定で特定済みの列を除外）"""
    mapped = set(data.auto_mapping.ignore_cols)
    if data.auto_mapping.class_col is not None:
        mapped.add(data.auto_mapping.class_col)
    if data.auto_mapping.number_col is not None:
        mapped.add(data.auto_mapping.number_col)
    if data.auto_mapping.name_col is not None:
        mapped.add(data.auto_mapping.name_col)

    return [i for i in range(len(data.headers)) if i not in mapped]


def convert_to_ocr_results(
    data: FormsCSVData,
    mapping: ColumnMapping,
    rubric: Rubric,
) -> tuple[list[StudentOcr], list[str]]:
    """CSVデータをStudentOcrのリストに変換する。

    Args:
        data: パース済みCSVデータ
        mapping: UIで確定した列マッピング
        rubric: 採点基準（question_id の対応付けに使用）

    Returns:
        (StudentOcrのリスト, エラーメッセージのリスト)
    """
    ocr_results: list[StudentOcr] = []
    errors: list[str] = []
    seen_ids: dict[str, int] = {}  # student_id → 出現回数（重複検出用）

    for row_idx, row in enumerate(data.rows):
        row_num = row_idx + 1

        # 学生名の構築
        name_parts = []
        class_val = ""
        number_val = ""

        if mapping.class_col is not None and mapping.class_col < len(row):
            class_val = row[mapping.class_col].strip()
            name_parts.append(class_val)
        if mapping.number_col is not None and mapping.number_col < len(row):
            number_val = row[mapping.number_col].strip()
            name_parts.append(number_val)
        if mapping.name_col is not None and mapping.name_col < len(row):
            name_val = row[mapping.name_col].strip()
            name_parts.append(name_val)

        # student_id: 組-番号 があればそれを使う、なければ連番
        if class_val and number_val:
            student_id = f"{class_val}-{number_val}"
        else:
            student_id = f"S{row_num:03d}"

        # 重複チェック（同じ組-番号が複数あるときはサフィックスを付ける）
        if student_id in seen_ids:
            seen_ids[student_id] += 1
            dup_num = seen_ids[student_id]
            errors.append(f"行{row_num}: ID '{student_id}' が重複しています（{dup_num}回目）")
            student_id = f"{student_id}_{dup_num}"
        else:
            seen_ids[student_id] = 1

        student_name = " ".join(name_parts) if name_parts else f"学生{row_num}"

        # 各設問の回答を OcrAnswer に変換
        answers: list[OcrAnswer] = []
        for question_id, col_idx in mapping.question_cols.items():
            if col_idx < len(row):
                text = row[col_idx].strip()
            else:
                text = ""
                errors.append(f"行{row_num}: 問{question_id} の列が見つかりません")

            answers.append(OcrAnswer(
                question_id=str(question_id),
                transcribed_text=text,
                confidence="high",  # テキストデータなので信頼度は高い
                manually_corrected=False,
            ))

        ocr_results.append(StudentOcr(
            student_id=student_id,
            student_name=student_name,
            page_numbers=[],  # 画像なし
            answers=answers,
            status="ocr_done",
        ))

    logger.info("CSV取り込み完了: %d名分", len(ocr_results))
    return ocr_results, errors

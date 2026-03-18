"""スキャンテスト.pdf の OCR ワークフロー検証スクリプト。

Usage:
    # デモモード（APIキー不要、OCR結果はダミー）
    python3 test_scan_ocr.py

    # Gemini で実際にOCR（2段構え + 高解像度埋込画像）
    python3 test_scan_ocr.py --provider gemini --api-key YOUR_KEY

    # Anthropic で実際にOCR
    python3 test_scan_ocr.py --provider anthropic --api-key YOUR_KEY
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from pdf_processor import pdf_to_images, split_pages_by_student, crop_regions_from_image
from models import Question, Rubric
from provider_factory import build_provider
import scoring_engine
from scoring_engine import parse_ocr_result


def main():
    parser = argparse.ArgumentParser(description="スキャンPDFのOCRテスト")
    parser.add_argument("--pdf", default=str(Path.home() / "Desktop" / "スキャンテスト.pdf"))
    parser.add_argument("--provider", default="demo", choices=["demo", "gemini", "anthropic"])
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--pages-per-student", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()

    # 1. PDF → 画像変換（埋込画像の直接抽出を試行）
    print(f"=== PDF読み込み: {args.pdf} ===")
    pdf_bytes = Path(args.pdf).read_bytes()
    images = pdf_to_images(pdf_bytes, dpi=args.dpi, prefer_embedded=True)
    print(f"  ページ数: {len(images)}")
    for i, img in enumerate(images):
        print(f"  ページ{i+1}: {img.size[0]}x{img.size[1]}px")

    # 比較: ラスタライズ版
    images_raster = pdf_to_images(pdf_bytes, dpi=args.dpi, prefer_embedded=False)
    for i, img in enumerate(images_raster):
        print(f"  (ラスタライズ比較) ページ{i+1}: {img.size[0]}x{img.size[1]}px")

    # 2. 学生ごとに分割
    groups = split_pages_by_student(images, pages_per_student=args.pages_per_student)
    print(f"\n=== 学生分割: {len(groups)}名 (1人あたり{args.pages_per_student}ページ) ===")

    # 3. テスト用の簡易ルーブリック
    rubric = Rubric(
        title="スキャンテスト",
        total_points=20,
        pages_per_student=args.pages_per_student,
        questions=[
            Question(id=1, description="問1", question_type="descriptive", max_points=10, scoring_criteria="テスト問題1"),
            Question(id=2, description="問2", question_type="descriptive", max_points=10, scoring_criteria="テスト問題2"),
        ],
    )
    print(f"\n=== ルーブリック: {rubric.title} ({len(rubric.questions)}問) ===")

    # 4. プロバイダー構築
    provider, provider_name = build_provider(
        args.provider, api_key=args.api_key, model_name=args.model
    )

    if args.provider == "demo":
        print("\n=== デモモード: OCRはダミー結果になります ===")
        print("=== 完了 ===")
        return

    # 5. レイアウト分析（2段構え 1回目）
    print(f"\n=== 2段構えOCR Step1: レイアウト分析 ===")
    layout = None
    try:
        first_images = [img for _, img in groups[0]]
        layout = provider.analyze_layout(images=first_images, rubric=rubric)
        print(f"  構造: {layout.get('overall_structure', 'N/A')}")
        for page_info in layout.get("pages", []):
            page_num = page_info.get("page_number", "?")
            print(f"\n  ページ{page_num}の領域:")
            for r in page_info.get("regions", []):
                bbox = r.get("bbox", "なし")
                print(f"    設問{r.get('question_id')}: {r.get('location')} | bbox={bbox}")
    except NotImplementedError:
        print(f"  {provider_name} はレイアウト分析に未対応")
    except Exception as e:
        print(f"  エラー: {e}")

    # 5.5 クロップ画像の確認・保存
    if layout:
        print(f"\n=== クロップ画像の確認 ===")
        for page_info in layout.get("pages", []):
            page_num = page_info.get("page_number", 1)
            page_idx = page_num - 1
            if 0 <= page_idx < len(images):
                crops = crop_regions_from_image(images[page_idx], page_info.get("regions", []))
                print(f"  ページ{page_num}: {len(crops)}個のクロップ")
                for qid, crop_img in crops:
                    print(f"    設問{qid}: {crop_img.size[0]}x{crop_img.size[1]}px")
                    # デバッグ用にクロップ画像を保存
                    out_path = Path(args.pdf).parent / f"crop_Q{qid}.png"
                    crop_img.save(str(out_path))
                    print(f"    → {out_path} に保存")

    # 6. OCR実行（2段構え 2回目: レイアウト情報 + クロップ画像付き）
    print(f"\n=== 2段構えOCR Step2: OCR実行 (provider={provider_name}) ===")
    for i, group in enumerate(groups):
        student_num = i + 1
        group_images = [img for _, img in group]
        print(f"\n--- 学生{student_num} ---")

        try:
            result = provider.ocr_student(images=group_images, rubric=rubric, layout=layout)

            # resultがdictかstrかで処理分岐
            if isinstance(result, str):
                print(f"  生のレスポンス ({len(result)}文字):")
                print("--- RAW START ---")
                print(result[:2000])
                print("--- RAW END ---")
                result = scoring_engine._extract_json(result)

            name, answers = parse_ocr_result(result, rubric)
            print(f"\n  【読み取り結果】")
            print(f"  氏名: {name or '(読み取れず)'}")
            for ans in answers:
                print(f"\n  === 設問{ans.question_id} (confidence: {ans.confidence}) ===")
                print(f"  {ans.transcribed_text}")
        except Exception as e:
            print(f"  エラー: {e}")
            import traceback
            traceback.print_exc()

    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()

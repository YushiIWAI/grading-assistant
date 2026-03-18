"""PDF処理モジュール: PDFを画像に変換し、学生ごとに分割する"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class PrivacyMaskConfig:
    """外部AI送信用の氏名マスキング設定。"""

    enabled: bool = True
    strategy: str = "top_right"
    width_ratio: float = 0.36
    height_ratio: float = 0.14
    margin_x_ratio: float = 0.03
    margin_y_ratio: float = 0.02
    first_page_only: bool = True
    fill_color: tuple[int, int, int] = (0, 0, 0)


def _clamp_ratio(value: float, default: float) -> float:
    """比率値を安全な範囲に丸める。"""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return min(max(numeric, 0.0), 1.0)


def mask_student_name(
    image: Image.Image,
    config: PrivacyMaskConfig | None = None,
) -> Image.Image:
    """氏名欄らしき領域を黒塗りしたコピーを返す。"""
    resolved = config or PrivacyMaskConfig()
    masked = image.copy()

    if not resolved.enabled:
        return masked

    width, height = masked.size
    mask_height = max(1, int(height * _clamp_ratio(resolved.height_ratio, 0.14)))
    margin_x = int(width * _clamp_ratio(resolved.margin_x_ratio, 0.03))
    margin_y = int(height * _clamp_ratio(resolved.margin_y_ratio, 0.02))
    y0 = min(max(0, margin_y), max(0, height - 1))
    y1 = min(height, y0 + mask_height)

    strategy = resolved.strategy
    if strategy == "top_band":
        x0 = 0
        x1 = width
    else:
        mask_width = max(1, int(width * _clamp_ratio(resolved.width_ratio, 0.36)))
        if strategy == "top_left":
            x0 = min(max(0, margin_x), max(0, width - 1))
            x1 = min(width, x0 + mask_width)
        else:
            x1 = max(1, width - margin_x)
            x0 = max(0, x1 - mask_width)

    ImageDraw.Draw(masked).rectangle((x0, y0, x1, y1), fill=resolved.fill_color)
    return masked


def mask_images_for_external_ai(
    images: list[Image.Image],
    config: PrivacyMaskConfig | None = None,
) -> list[Image.Image]:
    """外部AI送信用に画像をマスキングしたコピーを返す。"""
    resolved = config or PrivacyMaskConfig()
    masked_images: list[Image.Image] = []

    for index, image in enumerate(images):
        should_mask = resolved.enabled and (not resolved.first_page_only or index == 0)
        if should_mask:
            masked_images.append(mask_student_name(image, resolved))
        else:
            masked_images.append(image.copy())

    return masked_images


def _try_extract_page_image(
    doc: fitz.Document, page: fitz.Page, dpi: int,
) -> Image.Image | None:
    """ページに埋め込まれた画像を直接抽出する。

    スキャンPDFの場合、ページ全体を覆う1枚の画像が埋め込まれている。
    ラスタライズより高解像度の元画像をそのまま使えるため、OCR精度が向上する。
    抽出できない場合は None を返す。
    """
    page_images = page.get_images(full=True)
    if len(page_images) != 1:
        return None

    xref = page_images[0][0]
    try:
        extracted = doc.extract_image(xref)
    except Exception:
        return None

    img_width, img_height = extracted["width"], extracted["height"]

    # ラスタライズ時のサイズと比較し、埋込画像のほうが高解像度な場合のみ使う
    raster_width = page.rect.width * dpi / 72
    raster_height = page.rect.height * dpi / 72
    if img_width < raster_width * 0.9 and img_height < raster_height * 0.9:
        return None  # 埋込画像が小さい（ロゴ等）のでラスタライズのほうが良い

    try:
        img = Image.open(io.BytesIO(extracted["image"])).convert("RGB")
    except Exception:
        return None

    # ページの向きと画像の向きが合っているか確認（縦横比の差で判定）
    page_landscape = page.rect.width > page.rect.height
    img_landscape = img_width > img_height
    if page_landscape != img_landscape:
        img = img.rotate(90, expand=True)

    # ページに回転が設定されている場合も補正
    if page.rotation:
        img = img.rotate(-page.rotation, expand=True)

    return img


def pdf_to_images(
    pdf_bytes: bytes, dpi: int = 200, prefer_embedded: bool = True,
    submission_type: str = "handwritten",
) -> list[Image.Image]:
    """PDFの各ページをPIL Imageに変換する。

    prefer_embedded=True の場合、スキャンPDFの埋込画像を直接抽出して
    ラスタライズによる解像度劣化を回避する。

    submission_type="typed" の場合、埋込画像抽出をスキップし、
    低めのDPI（150）でラスタライズして処理を高速化する。
    """
    if submission_type == "typed":
        prefer_embedded = False
        dpi = min(dpi, 150)  # typed では 150dpi で十分
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    zoom = dpi / 72  # 72 DPI がデフォルト
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc[page_num]

        img = None
        if prefer_embedded:
            img = _try_extract_page_image(doc, page, dpi)

        if img is None:
            pix = page.get_pixmap(matrix=matrix)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        images.append(img)

    doc.close()
    return images


def crop_regions_from_image(
    image: Image.Image,
    regions: list[dict],
    padding_ratio: float = 0.02,
) -> list[tuple[str, Image.Image]]:
    """正規化bbox座標を使って、画像から各解答欄領域を切り出す。

    Args:
        image: 元のページ画像
        regions: レイアウト分析結果のリージョン（bbox: [x_min, y_min, x_max, y_max], 0.0~1.0）
        padding_ratio: 切り出し時の余白比率

    Returns:
        (question_id, cropped_image) のリスト。bboxがないリージョンはスキップ。
    """
    width, height = image.size
    results: list[tuple[str, Image.Image]] = []

    for region in regions:
        bbox = region.get("bbox")
        if not bbox or len(bbox) != 4:
            continue

        try:
            x_min, y_min, x_max, y_max = [float(v) for v in bbox]
        except (TypeError, ValueError):
            continue

        # 正規化座標(0-1)が範囲外なら補正
        x_min, y_min = max(0.0, x_min), max(0.0, y_min)
        x_max, y_max = min(1.0, x_max), min(1.0, y_max)
        if x_min >= x_max or y_min >= y_max:
            continue

        # パディングを追加
        pad_x = (x_max - x_min) * padding_ratio
        pad_y = (y_max - y_min) * padding_ratio
        x_min = max(0.0, x_min - pad_x)
        y_min = max(0.0, y_min - pad_y)
        x_max = min(1.0, x_max + pad_x)
        y_max = min(1.0, y_max + pad_y)

        # ピクセル座標に変換
        px_left = int(x_min * width)
        px_top = int(y_min * height)
        px_right = int(x_max * width)
        px_bottom = int(y_max * height)

        cropped = image.crop((px_left, px_top, px_right, px_bottom))
        qid = str(region.get("question_id", "?"))
        results.append((qid, cropped))

    return results


def split_pages_by_student(
    images: list[Image.Image],
    pages_per_student: int = 1,
) -> list[list[tuple[int, Image.Image]]]:
    """
    ページを学生ごとにグループ化する。

    Returns:
        list of student groups, each group is a list of (page_number, image) tuples.
        page_number is 1-indexed.
    """
    students = []
    for i in range(0, len(images), pages_per_student):
        group = []
        for j in range(pages_per_student):
            idx = i + j
            if idx < len(images):
                group.append((idx + 1, images[idx]))  # 1-indexed page number
        if group:
            students.append(group)
    return students


def image_to_base64(image: Image.Image, format: str = "PNG", max_size: int = 1600) -> str:
    """PIL ImageをBase64文字列に変換する（API送信用）。大きすぎる場合はリサイズ。"""
    # リサイズ（長辺を max_size に収める）
    w, h = image.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def image_to_bytes(image: Image.Image, format: str = "PNG") -> bytes:
    """PIL Imageをbytesに変換する（Streamlit表示用）"""
    buffer = io.BytesIO()
    image.save(buffer, format=format)
    return buffer.getvalue()


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    """PDFのページ数を取得する"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    count = len(doc)
    doc.close()
    return count

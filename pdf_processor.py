"""PDF処理モジュール: PDFを画像に変換し、学生ごとに分割する"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


def pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[Image.Image]:
    """PDFの各ページをPIL Imageに変換する"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    zoom = dpi / 72  # 72 DPI がデフォルト
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=matrix)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)

    doc.close()
    return images


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

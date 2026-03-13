"""pdf_processor.py のユニットテスト"""

from PIL import Image

from pdf_processor import (
    PrivacyMaskConfig,
    mask_images_for_external_ai,
    mask_student_name,
)


def _white_image(size=(100, 100)):
    return Image.new("RGB", size, color=(255, 255, 255))


class TestPrivacyMask:
    def test_mask_student_name_top_right(self):
        image = _white_image()
        config = PrivacyMaskConfig(
            enabled=True,
            strategy="top_right",
            width_ratio=0.3,
            height_ratio=0.2,
            margin_x_ratio=0.0,
            margin_y_ratio=0.0,
        )

        masked = mask_student_name(image, config)

        assert masked.getpixel((90, 10)) == (0, 0, 0)
        assert masked.getpixel((10, 10)) == (255, 255, 255)

    def test_mask_student_name_top_left(self):
        image = _white_image()
        config = PrivacyMaskConfig(
            enabled=True,
            strategy="top_left",
            width_ratio=0.3,
            height_ratio=0.2,
            margin_x_ratio=0.0,
            margin_y_ratio=0.0,
        )

        masked = mask_student_name(image, config)

        assert masked.getpixel((10, 10)) == (0, 0, 0)
        assert masked.getpixel((90, 10)) == (255, 255, 255)

    def test_mask_student_name_top_band(self):
        image = _white_image()
        config = PrivacyMaskConfig(
            enabled=True,
            strategy="top_band",
            height_ratio=0.1,
            margin_y_ratio=0.0,
        )

        masked = mask_student_name(image, config)

        assert masked.getpixel((50, 5)) == (0, 0, 0)
        assert masked.getpixel((50, 20)) == (255, 255, 255)

    def test_mask_images_only_first_page_by_default(self):
        images = [_white_image(), _white_image()]
        masked = mask_images_for_external_ai(
            images,
            PrivacyMaskConfig(first_page_only=True),
        )

        assert masked[0].getpixel((90, 10)) == (0, 0, 0)
        assert masked[1].getpixel((90, 10)) == (255, 255, 255)

    def test_mask_images_returns_copies_when_disabled(self):
        images = [_white_image()]
        masked = mask_images_for_external_ai(
            images,
            PrivacyMaskConfig(enabled=False),
        )

        assert masked[0] is not images[0]
        assert masked[0].getpixel((50, 10)) == (255, 255, 255)

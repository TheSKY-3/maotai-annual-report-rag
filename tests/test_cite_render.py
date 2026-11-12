"""轴4 高亮裁剪图测试：整行横带 + 红框，人眼可复核数字与行首标签。"""
import sys
from pathlib import Path

import pdfplumber
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cite_locate

RES = 150


def _red_rows(img: Image.Image) -> list[int]:
    px = img.load()
    rows = [y for y in range(img.height)
            if any(px[x, y][0] > 200 and px[x, y][1] < 80 and px[x, y][2] < 80
                   for x in range(img.width))]
    return rows


def _clusters(rows: list[int], gap: int = 5) -> int:
    return 1 + sum(1 for a, b in zip(rows, rows[1:]) if b - a > gap) if rows else 0


def test_render_crop_is_full_width_row_band(tmp_path):
    found = cite_locate.locate_number(2024, 26, "680.93")
    out = tmp_path / "p26.png"
    cite_locate.render_crop(2024, 26, [m.bbox for m in found], out)
    img = Image.open(out)
    with pdfplumber.open(cite_locate.RAW_DIR / "moutai_2024.pdf") as pdf:
        width = pdf.pages[25].width
    assert img.width == round(width * RES / 72)
    assert img.height < img.width / 4          # 行带，不是整页


def test_render_crop_marks_target_in_red(tmp_path):
    found = cite_locate.locate_number(2024, 26, "680.93")
    out = tmp_path / "p26.png"
    cite_locate.render_crop(2024, 26, [m.bbox for m in found], out)
    assert _red_rows(Image.open(out))


def test_render_crop_highlights_each_boxed_row(tmp_path):
    found = cite_locate.locate_number(2024, 64, "89,334,728,025.90")
    out = tmp_path / "p64.png"
    cite_locate.render_crop(2024, 64, [m.bbox for m in found], out)
    assert _clusters(_red_rows(Image.open(out))) == 2
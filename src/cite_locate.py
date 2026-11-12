"""轴4 坐标级引用：把答案里的数字定位到年报页面上的框。

页码与 PDF 页序号 1:1（scripts/extract_text.py）。定位用精确值匹配
（容差 0），避免 0.2% 接地容差把相邻单元格误框；百分数与表格裸数
仍按 src.numeric 的归一规则互认。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pdfplumber

from src import claims, numeric
from src.config import PROJECT

RAW_DIR = PROJECT / "data" / "raw_pdfs"
_ROW_TOL = 3.0
_EXACT = Decimal(0)
_NUM_TOKEN = re.compile(r"^[\d,，.\-]+$")


@dataclass(frozen=True)
class Located:
    bbox: tuple[float, float, float, float]   # x0, top, x1, bottom（PDF 坐标）
    row_text: str


def _page_words(year: int, page: int) -> list[dict]:
    with pdfplumber.open(RAW_DIR / f"moutai_{year}.pdf") as pdf:
        return pdf.pages[page - 1].extract_words()


def _match(target, n, declared: str | None) -> bool:
    if numeric.same_value(target, n, _EXACT):
        return True
    # 表头声明"单位：万元"而单元格裸数：答案带单位时按声明还原再比
    if declared and target.kind == "money" and n.kind == "plain" and not n.unit:
        return target.value == n.value * numeric.MONEY_UNITS[declared]
    return False


def _span_bbox(w: dict, chars: list[dict], start: int, end: int) -> tuple:
    """词内数字的字符跨度框；整词命中直接用词框。"""
    if start == 0 and end == len(w["text"]):
        return (w["x0"], w["top"], w["x1"], w["bottom"])
    wc = sorted((c for c in chars
                 if w["x0"] - .5 <= c["x0"] and c["x1"] <= w["x1"] + .5
                 and abs(c["top"] - w["top"]) < 1.5), key=lambda c: c["x0"])
    piece = w["text"][start:end]
    i = "".join(c["text"] for c in wc).find(piece)
    if i < 0:
        return (w["x0"], w["top"], w["x1"], w["bottom"])
    span = wc[i:i + len(piece)]
    return (min(c["x0"] for c in span), min(c["top"] for c in span),
            max(c["x1"] for c in span), max(c["bottom"] for c in span))


def locate_number(year: int, page: int, raw: str) -> list[Located]:
    """该页上所有等于 raw 的数字框，按页面自上而下排序。

    中文散文整句会被 pdfplumber 并成一个 word，故在词内按数字 token
    匹配并取字符跨度框；表格裸数按页头声明单位还原后匹配。
    """
    target = numeric.parse_token(raw)
    with pdfplumber.open(RAW_DIR / f"moutai_{year}.pdf") as pdf:
        p = pdf.pages[page - 1]
        words = p.extract_words()
        chars = p.chars
        declared = numeric.declared_unit(p.extract_text() or "")
    out: list[Located] = []
    for w in words:
        if target is None:
            spans = [(0, len(w["text"]))] if raw in w["text"] else []
        else:
            spans = [(n.start, n.end) for n in numeric.iter_numbers(w["text"])
                     if _match(target, n, declared)]
        for start, end in spans:
            row = sorted((x for x in words if abs(x["top"] - w["top"]) < _ROW_TOL),
                         key=lambda x: x["x0"])
            out.append(Located(_span_bbox(w, chars, start, end),
                               " ".join(x["text"] for x in row)))
    return out


def _label_tokens(row_text: str) -> list[str]:
    return [t for t in row_text.split() if not _NUM_TOKEN.fullmatch(t)]


def choose(matches: list[Located], evidence_text: str) -> Located | None:
    """同页多处出现时，选行首标签出现在被引片段里的那一行。

    无标签信号（续行、纯数字行）时返回 None，交由调用方决定整组高亮
    或记为歧义，不猜。
    """
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    best_score, best = 0, None
    for m in matches:
        score = sum(1 for t in _label_tokens(m.row_text) if t in evidence_text)
        if score > best_score:
            best_score, best = score, m
    return best


RESOLUTION = 150
_BAND_PAD = 12.0
_BOX_PAD = 2.0


def render_crop(year: int, page: int, boxes: list[tuple], out_path) -> Path:
    """整页宽的横带裁剪 + 红框：行首标签与数字同框，读者一眼复核。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(RAW_DIR / f"moutai_{year}.pdf") as pdf:
        p = pdf.pages[page - 1]
        im = p.to_image(resolution=RESOLUTION)
        for x0, top, x1, bottom in boxes:
            im.draw_rect((x0 - _BOX_PAD, top - _BOX_PAD,
                          x1 + _BOX_PAD, bottom + _BOX_PAD),
                         stroke="red", stroke_width=3)
        scale = RESOLUTION / 72
        band_top = max(0.0, min(b[1] for b in boxes) - _BAND_PAD)
        band_bottom = min(p.height, max(b[3] for b in boxes) + _BAND_PAD)
        crop = im.annotated.crop((0, int(band_top * scale),
                                  int(p.width * scale), int(band_bottom * scale)))
        crop.save(out_path)
    return out_path


def _safe_name(raw: str) -> str:
    return re.sub(r"[^\w.\-]", "_", raw)


def verify_citations(answer: str, evidence_text: str, out_dir, stem: str) -> list[dict]:
    """答案里每个指标数字 → 坐标 + 高亮图 + 是否歧义。

    数字取第一个含它的出处页；同页多处且无行首标签信号时整组高亮并
    记 ambiguous，不假装唯一。
    """
    spec = claims.citation_spec(answer)
    entries: list[dict] = []
    for number in spec["numbers"]:
        entry = {"number": number, "located": False, "ambiguous": False,
                 "year": None, "page": None, "boxes": [], "crop": None}
        for year, page in spec["pages"]:
            matches = locate_number(year, page, number)
            if not matches:
                continue
            picked = choose(matches, evidence_text)
            boxes = [picked.bbox] if picked else [m.bbox for m in matches]
            crop = render_crop(
                year, page, boxes,
                Path(out_dir) / f"{stem}_{year}p{page}_{_safe_name(number)}.png")
            entry.update(located=True, year=year, page=page, boxes=boxes,
                         ambiguous=picked is None and len(matches) > 1,
                         crop=str(crop))
            break
        entries.append(entry)
    return entries


def page_crop(year: int, page: int, numbers: list[str], out_dir) -> Path | None:
    """界面用：该页上所有被引数字的一张整行横带高亮图；无命中返回 None。"""
    boxes = [m.bbox for n in numbers for m in locate_number(year, page, n)]
    if not boxes:
        return None
    # 文件名用哈希：比较类答案含几十个数字，拼接会超 Windows 路径上限
    digest = hashlib.sha1("_".join(numbers).encode("utf-8")).hexdigest()[:12]
    out = Path(out_dir) / f"ui_{year}p{page}_{digest}.png"
    if out.exists():
        return out
    return render_crop(year, page, boxes, out)


def crop_for_source(hit: dict, spec: dict, out_dir) -> Path | None:
    """界面出处面板：只对被引页渲染高亮，避免每页都开 PDF。"""
    if not spec["numbers"] or (hit["year"], hit["page"]) not in set(spec["pages"]):
        return None
    return page_crop(hit["year"], hit["page"], spec["numbers"], out_dir)
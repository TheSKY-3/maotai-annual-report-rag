"""文本切块策略。

阶段 1 基线：按页内固定窗口切块（约 500 字、重叠 100 字）。
不跨页切块是有意为之：保证每块的出处精确到页，引用才可信。
E1 实验变体：结构化切块（标题边界，id 前缀 s-）与小块（父子块检索用，
id 前缀 m-），见 chunk_structured / chunk_small，与基线共用页清洗逻辑。
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    id: str
    text: str
    year: int
    page: int


def load_pages(extracted_dir: Path, year: int) -> list[tuple[int, str]]:
    """读取某一年的逐页文本，返回 [(页码, 文本)]，页码升序。"""
    pages = []
    for f in sorted((extracted_dir / str(year)).glob("page_*.txt")):
        page_no = int(f.stem.split("_")[1])
        text = f.read_text(encoding="utf-8")
        pages.append((page_no, text))
    return pages


def chunk_fixed(
    pages: list[tuple[int, str]],
    year: int,
    size: int = 500,
    overlap: int = 100,
    min_len: int = 30,
) -> list[Chunk]:
    """页内固定窗口切块：窗口 size，步长 size-overlap。

    页眉页脚（"X / N"）与过短碎片直接丢弃。
    """
    chunks: list[Chunk] = []
    step = size - overlap
    for page_no, text in pages:
        # 去掉页脚行（如 "13 / 143"）与页眉（首行含"年度报告"的行）
        body = "\n".join(
            line for line in text.splitlines()
            if "/" not in line and "年度报告" not in line
        ).strip()
        if len(body) < min_len:
            continue
        for start in range(0, len(body), step):
            piece = body[start:start + size].strip()
            if len(piece) < min_len:
                break
            cid = f"{year}-p{page_no:03d}-c{start // step:02d}"
            chunks.append(Chunk(id=cid, text=piece, year=year, page=page_no))
    return chunks


# ---------------------------------------------------------------------------
# E1 变体：结构化切块与小块（不影响上方基线 chunk_fixed / load_all_chunks）
# ---------------------------------------------------------------------------

def _clean_page(text: str) -> str:
    """页清洗，逻辑与 chunk_fixed 内联版完全一致：
    去掉页脚行（如 "13 / 143"）与页眉（含"年度报告"的行）。"""
    return "\n".join(
        line for line in text.splitlines()
        if "/" not in line and "年度报告" not in line
    ).strip()


# 行首标题/编号模式（对四份年报正文观察所得）：
#   第X节（第一节 释义）> 一、二、（一、经营情况讨论与分析）
#   > （一）/(一)（(一) 主要会计数据）> 1、（1、 利润表及现金流量表相关科目变动分析表）
#   > (1). /（4）（(1). 主营业务分行业……）
# 不含 A./B. 等字母行：它们多为释义/减值列表项而非章节标题，切开会过度碎片化。
# 目录页带点线（"...... 4"）的行不算标题。
_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+节"
    r"|[一二三四五六七八九十百]+、"
    r"|[（(][一二三四五六七八九十百]+[）)]"
    r"|[（(]\d+[）)]\.?"
    r"|\d+、)"
)
_DOT_LEADER_RE = re.compile(r"\.{4,}")
_SENTENCE_END = "。；"


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or _DOT_LEADER_RE.search(s):
        return False
    return bool(_HEADING_RE.match(s))


def _split_by_headings(body: str) -> list[str]:
    """在标题行处切段，标题行归入其后内容所在段的开头。"""
    segments: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if _is_heading(line) and current:
            segments.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        segments.append("\n".join(current).strip())
    return [seg for seg in segments if seg]


def _bisect_long(text: str, max_size: int) -> list[str]:
    """超过 max_size 的段按句边界（。；）二分；无句读（表格）则按长度硬切。"""
    if len(text) <= max_size:
        return [text]
    # 可用切分点：句末标点之后（排除末字符，保证两边都比原文短，必终止）
    breaks = [i + 1 for i, ch in enumerate(text[:-1]) if ch in _SENTENCE_END]
    if not breaks:
        return [text[i:i + max_size] for i in range(0, len(text), max_size)]
    mid = len(text) / 2
    pos = min(breaks, key=lambda p: abs(p - mid))
    return _bisect_long(text[:pos], max_size) + _bisect_long(text[pos:], max_size)


def chunk_structured(
    pages: list[tuple[int, str]],
    year: int,
    max_size: int = 800,
    min_len: int = 30,
) -> list[Chunk]:
    """按标题/编号边界切块（E1 变体，id 前缀 s-）。

    页内先按行首编号模式（第X节 / 一、 / （一） / 1、 / (1).）切段，
    单段超过 max_size 再按句边界（。；）二分；无句读的表格段按长度硬切。
    仍不跨页，页清洗与基线一致；短于 min_len 的碎片（孤立标题、
    "□适用 √不适用" 等样板）丢弃，与基线 min_len 语义一致。
    """
    chunks: list[Chunk] = []
    for page_no, text in pages:
        body = _clean_page(text)
        if len(body) < min_len:
            continue
        pieces: list[str] = []
        for seg in _split_by_headings(body):
            pieces.extend(p.strip() for p in _bisect_long(seg, max_size))
        idx = 0
        for piece in pieces:
            if len(piece) < min_len:
                continue
            chunks.append(Chunk(
                id=f"s-{year}-p{page_no:03d}-c{idx:02d}",
                text=piece, year=year, page=page_no,
            ))
            idx += 1
    return chunks


def chunk_small(
    pages: list[tuple[int, str]],
    year: int,
    size: int = 220,
    overlap: int = 50,
    min_len: int = 30,
) -> list[Chunk]:
    """页内小块切块（E1 变体，id 前缀 m-）：约 220 字、重叠 50 字。

    父子块方案中"检索用小块"：与 chunk_fixed 同一窗口算法与页清洗，
    仅参数与 id 命名空间不同；出处仍是所在页。
    """
    chunks: list[Chunk] = []
    step = size - overlap
    for page_no, text in pages:
        body = _clean_page(text)
        if len(body) < min_len:
            continue
        for start in range(0, len(body), step):
            piece = body[start:start + size].strip()
            if len(piece) < min_len:
                break
            chunks.append(Chunk(
                id=f"m-{year}-p{page_no:03d}-c{start // step:02d}",
                text=piece, year=year, page=page_no,
            ))
    return chunks


def load_all_chunks(extracted_dir: Path, years: list[int]) -> list[Chunk]:
    """对全部年份执行基线切块。"""
    all_chunks: list[Chunk] = []
    for year in years:
        pages = load_pages(extracted_dir, year)
        all_chunks.extend(chunk_fixed(pages, year))
    return all_chunks

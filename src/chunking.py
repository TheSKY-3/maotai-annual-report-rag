"""文本切块策略。

阶段 1 基线：按页内固定窗口切块（约 500 字、重叠 100 字）。
不跨页切块是有意为之：保证每块的出处精确到页，引用才可信。
后续实验（E1）在此文件扩展结构化切块与父子块。
"""
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


def load_all_chunks(extracted_dir: Path, years: list[int]) -> list[Chunk]:
    """对全部年份执行基线切块。"""
    all_chunks: list[Chunk] = []
    for year in years:
        pages = load_pages(extracted_dir, year)
        all_chunks.extend(chunk_fixed(pages, year))
    return all_chunks

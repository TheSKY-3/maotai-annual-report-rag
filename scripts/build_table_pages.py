# -*- coding: utf-8 -*-
"""E7 表格感知解析：用 pdfplumber 重建逐页文本，表格线性化为"键值句"。

背景: pypdf 把表格拍平后数字全部黏连（"茅台酒145,928,075,955.31
8,662,079,388.7894.06..."），检索和模型都读不懂。本脚本用 pdfplumber
的 find_tables() 按框线定位表格，表格外文本照常提取，表格转成逐行
"行首列；键 值；键 值"句子——数字不再黏连，行列关系显式保留。

参考: pdfplumber 官方文档；Elastic "PDF parsing in RAG"（表格先于切块
独立抽取、按行键值线性化）；保持表格不跨块（本表格感知文本仍走基线
500 字窗口切块，行句子天然是可切分点）。

用法: .venv/Scripts/python.exe scripts/build_table_pages.py
产物:
  data/extracted_tables/{年份}/page_XXX.txt   重建的逐页文本
  data/chunks/table.jsonl + data/index/table.npz  E7 切块与索引
支持断点续跑: 已存在的页面文件跳过。
"""
import json
import sys
import time
from pathlib import Path

import pdfplumber

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import EMBED_PATH, PROJECT as ROOT  # noqa: E402  # noqa: E402

RAW_DIR = ROOT / "data" / "raw_pdfs"
OUT_DIR = ROOT / "data" / "extracted_tables"
CHUNKS_OUT = ROOT / "data" / "chunks" / "table.jsonl"
INDEX_OUT = ROOT / "data" / "index" / "table.npz"


def _clean_cell(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace("\n", "").replace("\r", "").strip()


def table_to_text(data: list[list[str | None]]) -> str | None:
    """表格二维数组 → 线性化文本。

    - 全空行丢弃；仅首格有内容的行视为表格标题（如"主营业务分行业情况"）
    - 第一个多列行当表头；其后每行输出: 行首格：表头 值；表头 值；...
    - 单列表/复选框表（少于 2 列或少于 3 个有效格）丢弃
    """
    rows = [[_clean_cell(c) for c in r] for r in data]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return None
    ncol = max(len(r) for r in rows)
    if ncol < 2:
        return None
    filled = sum(1 for r in rows for c in r if c)
    if filled < 3:
        return None

    lines: list[str] = []
    header: list[str] = []
    group = ""
    for r in rows:
        nonempty = [c for c in r if c]
        if len(nonempty) == 1 and len(r) > 1 and not header:
            lines.append(f"【表格】{nonempty[0]}：")
            continue
        if not header:
            # 第一行多列数据行当作表头（表内标题行已单独输出）
            header = r + [""] * (ncol - len(r))
            continue
        r = r + [""] * (ncol - len(r))
        key = r[0] or (nonempty[0] if nonempty else "")
        # 分组小标题行（各列值与表头相同，如"分产品|营业收入|营业成本|..."）：
        # 不输出键值废话，改记组名，后续数据行带"组名·行首格"前缀
        if key and all(not r[i] or r[i] == header[i]
                       for i in range(1, ncol)):
            group = key
            continue
        pairs = "；".join(
            f"{header[i]} {r[i]}" for i in range(1, ncol) if r[i]
        )
        if pairs:
            prefix = f"{group}·{key}" if group else key
            lines.append(f"{prefix}：{pairs}")
    return "\n".join(lines) if len(lines) >= 2 else None


def rebuild_page(page) -> tuple[str, int]:
    """单页 → (重建文本, 表格数)。"""
    tables = page.find_tables()
    kept = 0
    parts: list[str] = []
    bboxes = []
    for t in tables:
        txt = table_to_text(t.extract())
        if txt:
            parts.append(txt)
            bboxes.append(t.bbox)
            kept += 1
    outside = page.filter(
        lambda obj: not any(
            b[0] - 1 <= obj["x0"] and obj["x1"] <= b[2] + 1
            and b[1] - 1 <= obj["top"] and obj["bottom"] <= b[3] + 1
            for b in bboxes
        )
    )
    text = (outside.extract_text() or "").strip()
    if parts:
        # 表格行内如有 ASCII 斜杠，替换为全角：基线切块会把含 "/" 的行
        # 当页脚（"9 / 143"）整行丢弃，不能让表格行误伤
        text = (text + "\n" + "\n".join(p.replace("/", "／")
                                        for p in parts)).strip()
    return text, kept


def main() -> None:
    t0 = time.time()
    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit("未找到 PDF，先运行 scripts/download_data.py")
    for pdf in pdfs:
        year = pdf.stem.split("_")[-1]
        out_dir = OUT_DIR / year
        out_dir.mkdir(parents=True, exist_ok=True)
        n_tables = 0
        with pdfplumber.open(pdf) as doc:
            for i, page in enumerate(doc.pages, start=1):
                out = out_dir / f"page_{i:03d}.txt"
                if out.exists():
                    continue
                text, k = rebuild_page(page)
                out.write_text(text, encoding="utf-8")
                n_tables += k
                if i % 50 == 0:
                    print(f"  {year} P{i}/{len(doc.pages)} "
                          f"({time.time() - t0:.0f}s)", flush=True)
        print(f"{year}: {i} 页重建, 提取有效表格 {n_tables} 个", flush=True)

    # ---- 切块（与基线同一算法）+ 向量索引 ----
    from src.chunking import chunk_fixed, load_pages
    from src.config import YEARS

    chunks = []
    for year in YEARS:
        pages = load_pages(OUT_DIR, year)
        chunks.extend(chunk_fixed(pages, year))
    print(f"E7 切块: {len(chunks)} 块")
    CHUNKS_OUT.write_text(
        "".join(json.dumps({"id": c.id, "text": c.text, "year": c.year,
                            "page": c.page}, ensure_ascii=False) + "\n"
                for c in chunks), encoding="utf-8")

    import numpy as np
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBED_PATH, device="cpu")
    emb = model.encode([c.text for c in chunks], batch_size=64,
                       show_progress_bar=True, normalize_embeddings=True)
    INDEX_OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(INDEX_OUT,
                        ids=np.array([c.id for c in chunks]),
                        embeddings=emb.astype(np.float32))
    print(f"索引 -> {INDEX_OUT}  总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

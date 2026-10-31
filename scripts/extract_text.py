"""逐页提取年报 PDF 文本。

用法: 在项目根目录运行  python scripts/extract_text.py
输出: data/extracted/{年份}/page_XXX.txt
说明: PDF 页码与年报印刷页码一致（首页即"1/127"），因此 PDF 页序号
      可直接用作引用出处（如"2024年报 P48"）。
"""
from pathlib import Path

from pypdf import PdfReader

RAW_DIR = Path("data/raw_pdfs")
OUT_DIR = Path("data/extracted")


def extract_one(pdf_path: Path) -> tuple[int, int]:
    """提取单份年报，返回 (页数, 总字符数)。"""
    year = pdf_path.stem.split("_")[-1]
    out_dir = OUT_DIR / year
    out_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(pdf_path)
    total_chars = 0
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        (out_dir / f"page_{i:03d}.txt").write_text(text, encoding="utf-8")
        total_chars += len(text)
    return len(reader.pages), total_chars


def main() -> None:
    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit("未找到 PDF，请先运行数据下载脚本")
    for pdf in pdfs:
        pages, chars = extract_one(pdf)
        print(f"{pdf.name}: {pages} 页, 共 {chars:,} 字符")


if __name__ == "__main__":
    main()

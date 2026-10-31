"""从巨潮资讯网下载贵州茅台年报（公开披露文件）。

用法: 在项目根目录运行  python scripts/download_data.py
输出: data/raw_pdfs/moutai_{年份}.pdf
说明: 下载地址来自 cninfo 公告接口，若失效请到 cninfo.com.cn
      搜索 600519 重新获取"年度报告"（中文完整版，非摘要/英文版）。
"""
from pathlib import Path
import urllib.request

BASE_URL = "http://static.cninfo.com.cn/"
RAW_DIR = Path("data/raw_pdfs")

# 年份 -> (公告标题, adjunctUrl)，均为巨潮"中文完整版年报"
REPORTS = {
    2022: ("贵州茅台2022年年度报告", "finalpage/2023-03-31/1216281757.PDF"),
    2023: ("贵州茅台2023年年度报告", "finalpage/2024-04-03/1219506510.PDF"),
    2024: ("贵州茅台2024年年度报告", "finalpage/2025-04-03/1222993920.PDF"),
    2025: ("贵州茅台2025年年度报告", "finalpage/2026-04-17/1225114741.PDF"),
}


def download(year: int, title: str, adjunct: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / f"moutai_{year}.pdf"
    if dest.exists() and dest.stat().st_size > 100_000:
        print(f"{dest.name} 已存在，跳过")
        return
    url = BASE_URL + adjunct
    print(f"下载 {title} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=300).read()
    if not data.startswith(b"%PDF"):
        raise SystemExit(f"{url} 返回的不是 PDF，接口可能已变更")
    dest.write_bytes(data)
    print(f"  -> {dest.name} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    for year, (title, adjunct) in sorted(REPORTS.items()):
        download(year, title, adjunct)
    print("全部完成")

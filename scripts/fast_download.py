"""多线程分段下载器：绕过单连接限速。

用法: python scripts/fast_download.py <URL> <输出路径> [线程数=16]
要求: 服务器支持 Range 请求（脚本会先探测）。
"""
import concurrent.futures
import sys
import urllib.request
from pathlib import Path


def head_info(url: str) -> tuple[int, bool]:
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        size = int(r.headers["Content-Length"])
        accepts = r.headers.get("Accept-Ranges") == "bytes"
    return size, accepts


def fetch_range(url: str, start: int, end: int, dest: Path, idx: int,
                retries: int = 20) -> None:
    """带断点续传与重试的单段下载：中途断开则从已写字节处继续。"""
    import time
    for attempt in range(retries):
        try:
            written = dest.stat().st_size if dest.exists() else 0
            total = end - start + 1
            if written >= total:
                return
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Range": f"bytes={start + written}-{end}"})
            with urllib.request.urlopen(req, timeout=60) as r, \
                    dest.open("ab") as f:
                while True:
                    block = r.read(1 << 20)
                    if not block:
                        break
                    f.write(block)
            if dest.stat().st_size >= total:
                return
        except Exception as e:  # noqa: BLE001
            print(f"  分段{idx} 第{attempt + 1}次重试: {e}", flush=True)
            time.sleep(3)
    raise SystemExit(f"分段{idx} 重试{retries}次仍失败")


def main() -> None:
    url, out = sys.argv[1], Path(sys.argv[2])
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 16

    size, accepts = head_info(url)
    print(f"总大小 {size / 1e6:.0f} MB, Range 支持: {accepts}", flush=True)
    if not accepts:
        raise SystemExit("服务器不支持分段下载")

    out.parent.mkdir(parents=True, exist_ok=True)
    bounds = [(i * size // threads, (i + 1) * size // threads - 1)
              for i in range(threads)]
    parts = [out.parent / f"{out.name}.part{i}" for i in range(threads)]

    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        futs = {
            pool.submit(fetch_range, url, s, e, p, i): i
            for i, ((s, e), p) in enumerate(zip(bounds, parts))
        }
        for fut in concurrent.futures.as_completed(futs):
            fut.result()
            i = futs[fut]
            print(f"  分段 {i + 1}/{threads} 完成", flush=True)

    with out.open("wb") as f:
        for p in parts:
            f.write(p.read_bytes())
            p.unlink()
    got = out.stat().st_size
    if got != size:
        raise SystemExit(f"合并后大小不符: {got} != {size}")
    print(f"完成 -> {out} ({got / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()

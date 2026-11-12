"""阶段3 并发压测：默认只打免费接口（/health 与缓存命中的 /cite_crop）。

/ask 花钱：须显式 --ask N 且 --yes，运行前打印成本估算。
用法:
  .venv/Scripts/python.exe -X utf8 scripts/load_test.py --base http://127.0.0.1:8001
  ... --concurrency 16 --requests 96
产物: results/load_test.json
"""
import argparse
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import RESULTS_DIR  # noqa: E402

CROP = "/cite_crop?year=2024&page=26&numbers=680.93"


def summarize(latencies: list[float], statuses: list[int], wall_s: float) -> dict:
    n = len(latencies)
    ordered = sorted(latencies)

    def pct(p: float):
        if not ordered:
            return None
        return ordered[min(n - 1, math.ceil(p * n) - 1)]

    errors = sum(1 for s in statuses if s is None or s >= 400)
    return {
        "n": n,
        "errors": errors,
        "error_rate": round(errors / n, 4) if n else None,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "rps": round(n / wall_s, 2) if wall_s > 0 else None,
    }


def _one(base: str, path: str) -> tuple[float, int | None]:
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            r.read()
            return (time.perf_counter() - t) * 1000, r.status
    except Exception:
        return (time.perf_counter() - t) * 1000, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--requests", type=int, default=96)
    ap.add_argument("--ask", type=int, default=0, help="额外打 N 次 /ask（花钱）")
    ap.add_argument("--yes", action="store_true", help="确认 /ask 成本")
    args = ap.parse_args()

    if args.ask and not args.yes:
        print(f"/ask {args.ask} 次约 ¥{args.ask * 0.002:.3f}（峰时翻倍）；加 --yes 才跑")
        return

    paths = ["/health" if i % 2 else CROP for i in range(args.requests)]
    lat: list[float] = []
    sts: list[int | None] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for ms, st in pool.map(lambda p: _one(args.base, p), paths):
            lat.append(ms)
            sts.append(st)
    wall = time.perf_counter() - started
    out = summarize(lat, sts, wall)
    out.update({"base": args.base, "concurrency": args.concurrency,
                "paths": "health+cite_crop", "wall_s": round(wall, 2)})

    if args.ask:
        ask_lat, ask_sts = [], []
        body = json.dumps({"question": "2024年营业总收入是多少？"}).encode("utf-8")
        for _ in range(args.ask):
            t = time.perf_counter()
            try:
                req = urllib.request.Request(
                    args.base + "/ask", data=body,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    r.read()
                    ask_sts.append(r.status)
            except Exception:
                ask_sts.append(None)
            ask_lat.append((time.perf_counter() - t) * 1000)
        out["ask"] = summarize(ask_lat, ask_sts, sum(ask_lat) / 1000)

    (RESULTS_DIR / "load_test.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
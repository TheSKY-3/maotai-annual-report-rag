"""数据自包含：首次启动自动补齐模型/PDF/原文页/表格页，幂等可重跑。

索引（contextual.npz/jsonl）随仓库分发，缺失即克隆不完整，不自动重建（花钱）。
模型经 hf-mirror 下载并做 sha256 校验；PDF/原文页/表格页复用现有脚本。
用法:
  python scripts/prepare_data.py --check   # 只报缺什么
  python scripts/prepare_data.py           # 缺什么补什么
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
YEARS = (2022, 2023, 2024, 2025)
MODEL_REL = "data/models/bge-small-zh-v1.5"
MODEL_SHA256 = "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
HF_ENDPOINT = "https://hf-mirror.com"


def missing_artifacts(root: Path) -> list[str]:
    missing = []
    if not (root / MODEL_REL / "model.safetensors").is_file():
        missing.append("model")
    if not all((root / "data" / "raw_pdfs" / f"moutai_{y}.pdf").is_file()
               for y in YEARS):
        missing.append("pdfs")
    if not all((root / "data" / "extracted" / str(y) / "page_001.txt").is_file()
               for y in YEARS):
        missing.append("extracted")
    if not all((root / "data" / "extracted_tables_plus" / str(y) /
                "page_001.txt").is_file() for y in YEARS):
        missing.append("tables_plus")
    if not ((root / "data" / "index" / "contextual.npz").is_file()
            and (root / "data" / "chunks" / "contextual.jsonl").is_file()):
        missing.append("index")
    return missing


def _download_model(dest: Path, _snapshot=None, _sleep=None) -> None:
    import os
    import time
    _snapshot = _snapshot or _hf_snapshot
    _sleep = _sleep or time.sleep
    # 镜像 HEAD 请求偶发超时：放宽超时并退避重试三次
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "300")
    for attempt in range(3):
        try:
            _snapshot("BAAI/bge-small-zh-v1.5", local_dir=str(dest),
                      endpoint=HF_ENDPOINT)
            break
        except Exception:
            if attempt == 2:
                raise
            _sleep(5 * (attempt + 1))
    got = hashlib.sha256((dest / "model.safetensors").read_bytes()).hexdigest()
    if got != MODEL_SHA256:
        raise SystemExit(f"模型校验失败：{got} != {MODEL_SHA256}")


def _hf_snapshot(repo: str, local_dir: str, endpoint: str) -> None:
    from huggingface_hub import snapshot_download
    snapshot_download(repo, local_dir=local_dir, endpoint=endpoint)


_SEED_RELS = {
    "model": MODEL_REL,
    "pdfs": "data/raw_pdfs",
    "extracted": "data/extracted",
    "tables_plus": "data/extracted_tables_plus",
}


def ensure(root: Path, run=None, download_model=None, seed=None) -> list[str]:
    """缺什么补什么：在线优先；失败时若给了离线种子包则从种子复制。"""
    import shutil
    run = run or (lambda cmd: subprocess.run(cmd, cwd=root, check=True))
    download_model = download_model or _download_model
    scripts = PROJECT / "scripts"
    done = []
    for kind in missing_artifacts(root):
        src = seed / _SEED_RELS[kind] if seed else None
        try:
            if kind == "model":
                download_model(root / MODEL_REL)
            elif kind == "pdfs":
                run([sys.executable, str(scripts / "download_data.py")])
            elif kind == "extracted":
                run([sys.executable, str(scripts / "extract_text.py")])
            else:
                # index / tables_plus 随仓库分发，无 committed 重建脚本
                raise SystemExit(f"{kind} 随仓库分发；缺失请重新克隆或用 --seed")
        except (Exception, SystemExit):
            if src is None or not src.exists():
                raise
            shutil.copytree(src, root / _SEED_RELS[kind], dirs_exist_ok=True)
            if kind == "model":
                got = hashlib.sha256(
                    (root / MODEL_REL / "model.safetensors").read_bytes()
                ).hexdigest()
                if got != MODEL_SHA256:
                    raise SystemExit(f"种子模型校验失败：{got}")
        done.append(kind)
    return done


def main() -> None:
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--seed", default=os.environ.get("RAG_DATA_SEED"),
                    help="离线种子仓库根目录（含 data/；在线下载失败时回退复制）")
    args = ap.parse_args()
    missing = missing_artifacts(PROJECT)
    if args.check:
        print("missing:", missing or "none")
        raise SystemExit(1 if missing else 0)
    seed = Path(args.seed) if args.seed else None
    done = ensure(PROJECT, seed=seed)
    print("prepared:", done or "nothing (already complete)")


if __name__ == "__main__":
    main()
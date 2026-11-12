"""数据自包含：缺什么补什么，幂等；模型带 sha256 校验。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_data as pd


def _touch(root: Path, rel: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_empty_root_reports_all_missing(tmp_path):
    assert set(pd.missing_artifacts(tmp_path)) == {
        "model", "pdfs", "extracted", "tables_plus", "index"}


def test_complete_root_reports_nothing(tmp_path):
    _touch(tmp_path, "data/models/bge-small-zh-v1.5/model.safetensors")
    for y in (2022, 2023, 2024, 2025):
        _touch(tmp_path, f"data/raw_pdfs/moutai_{y}.pdf")
        _touch(tmp_path, f"data/extracted/{y}/page_001.txt")
        _touch(tmp_path, f"data/extracted_tables_plus/{y}/page_001.txt")
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")
    assert pd.missing_artifacts(tmp_path) == []


def test_partial_root_reports_only_missing(tmp_path):
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")
    assert pd.missing_artifacts(tmp_path) == [
        "model", "pdfs", "extracted", "tables_plus"]


def test_ensure_runs_only_missing_steps(tmp_path):
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")
    _touch(tmp_path, "data/models/bge-small-zh-v1.5/model.safetensors")
    for y in (2022, 2023, 2024, 2025):
        _touch(tmp_path, f"data/extracted_tables_plus/{y}/page_001.txt")
    calls = []
    done = pd.ensure(tmp_path, run=lambda cmd: calls.append(Path(cmd[-1]).name),
                     download_model=lambda dest: calls.append("model"))
    assert done == ["pdfs", "extracted"]
    assert calls == ["download_data.py", "extract_text.py"]


def test_ensure_complete_root_is_noop(tmp_path):
    for rel in ["data/models/bge-small-zh-v1.5/model.safetensors",
                "data/index/contextual.npz", "data/chunks/contextual.jsonl"]:
        _touch(tmp_path, rel)
    for y in (2022, 2023, 2024, 2025):
        _touch(tmp_path, f"data/raw_pdfs/moutai_{y}.pdf")
        _touch(tmp_path, f"data/extracted/{y}/page_001.txt")
        _touch(tmp_path, f"data/extracted_tables_plus/{y}/page_001.txt")
    calls = []
    assert pd.ensure(tmp_path, run=lambda c: calls.append(c),
                     download_model=lambda d: calls.append(d)) == []
    assert calls == []


def test_download_model_retries_transient_failures(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(pd, "MODEL_SHA256", hashlib.sha256(b"x").hexdigest())
    tries = []

    def flaky(repo, local_dir, endpoint):
        tries.append(1)
        if len(tries) < 3:
            raise ConnectionError("head call flake")
        (tmp_path / "model.safetensors").write_bytes(b"x")

    pd._download_model(tmp_path, _snapshot=flaky, _sleep=lambda s: None)
    assert len(tries) == 3


def test_download_model_gives_up_after_three_tries(tmp_path):
    def always_fail(repo, local_dir, endpoint):
        raise ConnectionError("down")

    try:
        pd._download_model(tmp_path, _snapshot=always_fail, _sleep=lambda s: None)
    except ConnectionError:
        pass
    else:
        raise AssertionError("should re-raise after retries")


def test_ensure_falls_back_to_offline_seed(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(pd, "MODEL_SHA256", hashlib.sha256(b"x").hexdigest())
    seed = tmp_path / "seed"
    _touch(seed, "data/models/bge-small-zh-v1.5/model.safetensors")
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")
    for y in (2022, 2023, 2024, 2025):
        _touch(seed, f"data/raw_pdfs/moutai_{y}.pdf")
        _touch(seed, f"data/extracted/{y}/page_001.txt")
        _touch(seed, f"data/extracted_tables_plus/{y}/page_001.txt")

    def boom(cmd):
        raise ConnectionError("network down")

    done = pd.ensure(tmp_path, run=boom,
                     download_model=lambda d: (_ for _ in ()).throw(
                         ConnectionError("hf down")),
                     seed=seed)
    assert done == ["model", "pdfs", "extracted", "tables_plus"]
    assert (tmp_path / "data/models/bge-small-zh-v1.5/model.safetensors").is_file()
    assert (tmp_path / "data/raw_pdfs/moutai_2024.pdf").is_file()


def test_ensure_without_seed_re_raises(tmp_path):
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")

    def boom(cmd):
        raise ConnectionError("network down")

    try:
        pd.ensure(tmp_path, run=boom,
                  download_model=lambda d: (_ for _ in ()).throw(
                      ConnectionError("hf down")))
    except ConnectionError:
        pass
    else:
        raise AssertionError("should re-raise without seed")


def test_seed_model_must_pass_checksum(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(pd, "MODEL_SHA256", hashlib.sha256(b"good").hexdigest())
    seed = tmp_path / "seed"
    _touch(seed, "data/models/bge-small-zh-v1.5/model.safetensors")
    (seed / "data/models/bge-small-zh-v1.5/model.safetensors").write_bytes(b"bad")
    _touch(tmp_path, "data/index/contextual.npz")
    _touch(tmp_path, "data/chunks/contextual.jsonl")
    try:
        pd.ensure(tmp_path, run=lambda c: None,
                  download_model=lambda d: (_ for _ in ()).throw(
                      ConnectionError("hf down")), seed=seed)
    except SystemExit:
        pass
    else:
        raise AssertionError("corrupt seed model must fail checksum")
"""阶段3：公网预备的最小护栏——可选 Bearer 鉴权 + 每 IP 每分钟限流。

未设 RAG_API_TOKEN / RAG_RATE_LIMIT 时行为与本地开发完全一致（开放）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import api

client = TestClient(api.app)
CROP = {"year": 2024, "page": 26, "numbers": "680.93"}


@pytest.fixture(autouse=True)
def _clear_buckets():
    api.RATE_BUCKETS.clear()
    yield
    api.RATE_BUCKETS.clear()


def test_open_when_no_token(monkeypatch):
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    assert client.get("/cite_crop", params=CROP).status_code == 200


def test_401_without_token_when_set(monkeypatch):
    monkeypatch.setenv("RAG_API_TOKEN", "s3cret")
    assert client.get("/cite_crop", params=CROP).status_code == 401


def test_401_with_wrong_token(monkeypatch):
    monkeypatch.setenv("RAG_API_TOKEN", "s3cret")
    r = client.get("/cite_crop", params=CROP,
                   headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_200_with_correct_token(monkeypatch):
    monkeypatch.setenv("RAG_API_TOKEN", "s3cret")
    r = client.get("/cite_crop", params=CROP,
                   headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_health_stays_open_under_token(monkeypatch):
    monkeypatch.setenv("RAG_API_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200


def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    monkeypatch.setenv("RAG_RATE_LIMIT", "2")
    codes = [client.get("/cite_crop", params=CROP).status_code for _ in range(2)]
    assert codes == [200, 200]
    r = client.get("/cite_crop", params=CROP)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_rate_limit_per_ip_not_global(monkeypatch):
    monkeypatch.delenv("RAG_API_TOKEN", raising=False)
    monkeypatch.setenv("RAG_RATE_LIMIT", "1")
    assert client.get("/cite_crop", params=CROP).status_code == 200
    other = TestClient(api.app, client=("10.0.0.9", 1234))
    assert other.get("/cite_crop", params=CROP).status_code == 200
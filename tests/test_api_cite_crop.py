"""轴4 坐标图 HTTP 接口：前端出处面板消费 /cite_crop 取高亮 PNG。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src.api import app

client = TestClient(app)


def test_cite_crop_returns_png():
    r = client.get("/cite_crop", params={"year": 2024, "page": 26,
                                         "numbers": "680.93"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_cite_crop_absent_number_is_404():
    r = client.get("/cite_crop", params={"year": 2024, "page": 26,
                                         "numbers": "12345.67"})
    assert r.status_code == 404


def test_cite_crop_bad_year_is_400():
    r = client.get("/cite_crop", params={"year": 1999, "page": 26,
                                         "numbers": "680.93"})
    assert r.status_code == 400


def test_cite_crop_empty_numbers_is_400():
    r = client.get("/cite_crop", params={"year": 2024, "page": 26,
                                         "numbers": ""})
    assert r.status_code == 400
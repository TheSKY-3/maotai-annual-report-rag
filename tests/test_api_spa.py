"""阶段2：FastAPI 单端口交付——根路径服务前端构建产物，API 路由不被吞。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src.api import app

client = TestClient(app)


def test_root_serves_spa_index():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in r.text


def test_api_routes_not_swallowed_by_spa():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
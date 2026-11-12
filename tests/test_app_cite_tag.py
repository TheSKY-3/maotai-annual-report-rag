"""出处 tag 渲染：模型偶发全角冒号「[来源：…]」，须与半角同样渲染成 pill。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import app


def test_fmt_answer_fullwidth_colon_becomes_pill():
    out = app.fmt_answer("合计为680.93。 [来源：2024年报 P26]")
    assert 'class="mt-cite"' in out
    assert "2024年报 P26" in out
    assert "[来源" not in out


def test_fmt_answer_ascii_colon_becomes_pill():
    out = app.fmt_answer("合计为680.93。 [来源: 2024年报 P26]")
    assert 'class="mt-cite"' in out
    assert "[来源" not in out


def test_fmt_answer_escapes_html():
    out = app.fmt_answer("<script>x</script> [来源: 2024年报 P26]")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
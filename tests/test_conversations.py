"""P1 会话持久化存储：ConversationStore 接口 + SQLite 实现。

单用户/同浏览器/匿名会话号；meta JSON 存富响应以便原样恢复；
session 隔离；删除级联清消息。测试用内存 SQLite，不起服务。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.conversations import SqliteConversationStore


@pytest.fixture
def store():
    s = SqliteConversationStore(":memory:")
    yield s
    s.close()


def test_create_returns_id_and_lists(store):
    cid = store.create("sess-A", "茅台净利润")
    assert isinstance(cid, str) and cid
    items = store.list("sess-A")
    assert len(items) == 1
    assert items[0].id == cid
    assert items[0].title == "茅台净利润"


def test_list_isolated_by_session(store):
    store.create("sess-A", "a")
    store.create("sess-B", "b")
    assert len(store.list("sess-A")) == 1
    assert len(store.list("sess-B")) == 1
    assert store.list("sess-A")[0].title == "a"


def test_append_and_get_roundtrip_meta(store):
    cid = store.create("sess-A")
    store.append(cid, "user", "2024净利润?", {})
    store.append(cid, "assistant", "123亿 [来源: 2024年报 P1]",
                 {"citations": ["2024年报 P1"], "cost_cny": 0.002})
    msgs = store.get(cid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "123亿 [来源: 2024年报 P1]"
    assert msgs[1].meta["citations"] == ["2024年报 P1"]
    assert msgs[1].meta["cost_cny"] == 0.002


def test_list_ordered_by_recent_update(store):
    c1 = store.create("sess-A", "first")
    c2 = store.create("sess-A", "second")
    store.append(c1, "user", "new activity", {})
    items = store.list("sess-A")
    assert items[0].id == c1
    assert items[1].id == c2


def test_delete_removes_conversation_and_messages(store):
    cid = store.create("sess-A")
    store.append(cid, "user", "q", {})
    store.delete(cid)
    assert store.list("sess-A") == []
    assert store.get(cid) == []


def test_rename_updates_title(store):
    cid = store.create("sess-A", "old")
    store.rename(cid, "new")
    assert store.list("sess-A")[0].title == "new"


def test_get_missing_conversation_returns_empty(store):
    assert store.get("no-such-id") == []


def test_owner_returns_session_or_none(store):
    cid = store.create("sess-A")
    assert store.owner(cid) == "sess-A"
    assert store.owner("no-such-id") is None
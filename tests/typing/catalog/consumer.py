"""Static-only fixture: producers must satisfy the existing browser consumer."""

from typing import Any, assert_type

from amplifier_web.browser_state import navigation
from amplifier_web.chat_navigation import snapshot
from amplifier_web.history_directory import HistoryDirectory
from amplifier_web.history_directory_query import chats


def consumer(state: dict[str, Any], directory: HistoryDirectory) -> None:
    navigation(state, chats=snapshot)
    navigation(state, chats=directory.chats)
    navigation(state, chats=lambda view: chats(directory, view))
    for page in (snapshot(state), directory.chats(state), chats(directory, state)):
        assert_type(page["total"], int)
        assert_type(page["items"], list[dict[str, Any]])
        assert_type(page["activityCounts"], dict[str, int])
        if "index" in page:
            assert_type(page["index"], int)

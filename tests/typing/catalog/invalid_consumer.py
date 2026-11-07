"""Static-only negative fixture. Each marked line must produce a diagnostic."""

from typing import Any

from amplifier_web.browser_state import navigation
from amplifier_web.history_directory import HistoryDirectory
from amplifier_web.history_directory_query import chats


def invalid_consumer(state: dict[str, Any], directory: HistoryDirectory) -> None:
    navigation(
        state,
        chats=lambda view: chats(directory, view)["items"],  # expected-error
    )
    page = chats(directory, state, section="shortcuts")
    page["total"] = "many"  # expected-error
    print(page["totla"])  # expected-error
    print(page["index"])  # expected-error: shortcuts do not promise a page index

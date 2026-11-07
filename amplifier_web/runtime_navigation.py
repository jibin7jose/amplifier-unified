"""Known Chat controls targets shared by browser and agent view.update."""
SECTIONS = {
    "overview": "overview", "direction": "direction", "limits": "limits", "tools": "tools",
    "computer": "computer", "screen-source": "computer", "desktop-host": "computer", "capture": "computer",
    "schedules": "direction",
}


def update(view, patch):
    if "runtimeDraft" not in patch:
        return patch
    from .errors import AppError
    change = patch["runtimeDraft"]
    if not isinstance(change, dict):
        raise AppError("Chat controls settings must be an object.")
    draft = {**view.get("runtimeDraft", {}), **change}
    if draft.get("tab", "overview") not in set(SECTIONS.values()):
        raise AppError("Choose a known Chat controls tab.")
    if "section" in change and change["section"] is not None:
        section = change["section"]
        if not isinstance(section, str) or section not in SECTIONS:
            raise AppError("Choose a known Chat controls section.")
        if "tab" in change and change["tab"] != SECTIONS[section]:
            raise AppError("The section belongs to a different Chat controls tab.")
        draft["tab"] = SECTIONS[section]
        # Server-owned revision makes repeat reveals observable, without
        # accepting arbitrary CSS selectors or browser script from an agent.
        draft["revealRevision"] = view.get("runtimeDraft", {}).get("revealRevision", 0)+1
        patch = {**patch, "panel": "chat-work" if section == "schedules" else "runtime"}
    else:
        draft["revealRevision"] = view.get("runtimeDraft", {}).get("revealRevision", 0)
        if "tab" in change:
            draft["section"] = None
    return {**patch, "runtimeDraft": draft}

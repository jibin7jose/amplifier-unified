"""Recognize uv's copied Windows entry point, not arbitrary same-name programs.

This is an installation ownership check, not a sandbox against the same account.
Native reads use the existing pinned, private, local-NTFS boundary. A uv receipt
alone, equal bytes alone, or a matching interpreter alone is insufficient.
"""
from pathlib import Path
import tomllib

from . import windows_files


def validate_launcher(prefix: Path, bin_root: Path, executable: str) -> None:
    """Require the receipt-bound private public copy and its environment original."""
    prefix = windows_files.address(prefix)
    bin_root = windows_files.address(bin_root)
    launcher = prefix / "Scripts/amplifier-unified.exe"
    public = bin_root / launcher.name
    selected = windows_files.address(executable)
    # WindowsPath equality case-folds. The private-file owner intentionally
    # rejects component case aliases, including collisions in case-sensitive
    # directories; only address()'s drive normalization is permitted here.
    if str(selected) not in (str(launcher), str(public)):
        raise ValueError("The Windows launcher is outside the active uv tool installation")

    # Keep the roots/ancestors pinned across the joined receipt and byte checks.
    # No create/repair: unsafe existing permissions, reparse points, hardlinks,
    # missing/oversized files and non-NTFS paths all retain their refusal.
    with (
        windows_files.directory(prefix, owned_root=prefix.parent) as installation,
        windows_files.directory(launcher.parent, owned_root=prefix.parent) as scripts,
        windows_files.directory(bin_root, owned_root=bin_root) as entrypoints,
    ):
        raw = installation.read_private("uv-receipt.toml", limit=1024 * 1024)
        receipt = tomllib.loads(raw.decode("utf-8"))
        tool = receipt.get("tool")
        entries = tool.get("entrypoints") if isinstance(tool, dict) else None
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ValueError("The Windows uv tool receipt has no valid entry points")
        matches = [row for row in entries if row.get("name") == "amplifier-unified"]
        if (len(matches) != 1 or matches[0].get("from") != "amplifier-unified"
                or not isinstance(matches[0].get("install-path"), str)
                or str(windows_files.address(matches[0]["install-path"])) != str(public)):
            raise ValueError("The Windows uv tool receipt does not own this public launcher")
        original = scripts.read_private(launcher.name, limit=1024 * 1024)
        copied = entrypoints.read_private(public.name, limit=1024 * 1024)
        if not original.startswith(b"MZ") or copied != original:
            raise ValueError("The Windows uv public launcher differs from its installed entry point")
        if (installation.read_private("uv-receipt.toml", limit=1024 * 1024) != raw
                or scripts.read_private(launcher.name, limit=1024 * 1024) != original
                or entrypoints.read_private(public.name, limit=1024 * 1024) != copied):
            raise ValueError("The Windows uv installation changed during launcher verification")
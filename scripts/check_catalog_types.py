"""Check one navigation boundary and prove that malformed consumers are rejected."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checker",
        nargs="+",
        default=["pyright"],
        help="Pyright command, optionally including its interpreter",
    )
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / "tests/typing/catalog"
    version = subprocess.run(
        [*args.checker, "--version"], check=True, capture_output=True, text=True
    )
    print(version.stdout.strip())
    for name, invalid in [("consumer.py", False), ("invalid_consumer.py", True)]:
        source = fixture / name
        expected = {
            number
            for number, line in enumerate(source.read_text().splitlines(), 1)
            if "# expected-error" in line
        }
        checked = subprocess.run(
            [
                *args.checker,
                "--project",
                str(fixture / "pyrightconfig.json"),
                "--outputjson",
                str(source),
                str(fixture.parents[2] / "amplifier_web/history_directory_query.py"),
            ],
            capture_output=True,
            text=True,
        )
        if checked.returncode not in (0, 1):
            raise RuntimeError(checked.stderr or checked.stdout)
        report = json.loads(checked.stdout)
        errors = [
            item for item in report["generalDiagnostics"] if item["severity"] == "error"
        ]
        actual = {
            item["range"]["start"]["line"] + 1
            for item in errors
            if Path(item["file"]).resolve() == source.resolve()
        }
        if (
            checked.returncode != int(invalid)
            or actual != expected
            or any(Path(item["file"]).resolve() != source.resolve() for item in errors)
            or bool(errors) != invalid
        ):
            print(checked.stdout)
            if checked.stderr:
                print(checked.stderr, file=sys.stderr)
            return 1
        print(f"{name}: {len(errors)} expected errors; no unexpected errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Keep manual interpreter selection aligned with the executable installer."""

from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallationGuidanceTests(unittest.TestCase):
    def test_manual_installs_select_the_installers_python(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        selected = re.search(r'python install (\d+\.\d+)', installer)
        self.assertIsNotNone(selected)
        python = selected.group(1)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        commands = [
            shlex.split(line)
            for line in readme.splitlines()
            if line.startswith("uv tool install ")
        ]
        self.assertGreaterEqual(len(commands), 4)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn("--python", command)
                self.assertEqual(command[command.index("--python") + 1], python)

    def test_design_does_not_promote_unqualified_native_support(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("**Native Windows is not yet supported.**", readme)
        self.assertIn("docs/design/native-windows-installation.md", readme)
        design = ROOT / "docs/design/native-windows-installation.md"
        self.assertTrue(design.is_file())
        self.assertIn("not a native Windows support claim", design.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
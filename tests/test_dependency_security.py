"""Host/worker security floors and opt-in offline real-resolver regression.

The integration test needs official wheels (old and fixed Pillow/filelock plus
the artifact group) in AMPLIFIER_SECURITY_WHEELS. It never accesses the network
or a serving runtime. The projection deliberately qualifies these dependencies,
not the unrelated Git provider/bundle graph or live adoption.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

from packaging.requirements import Requirement
from packaging.version import Version
import pytest

from amplifier_web import runtime_environment as environments


ROOT = Path(__file__).resolve().parents[1]
FLOORS = {"pillow": ("12.3.0", "13.0.0", "14"), "filelock": ("3.32.7", "4.2.1", "5")}


def requirements(values):
    return {item.name.lower(): item for item in map(Requirement, values)}


def test_host_worker_and_retained_locks_exclude_vulnerable_versions():
    host = tomllib.loads((ROOT / "pyproject.toml").read_text())
    worker = tomllib.loads(environments.manifest_path().read_text())
    host_requirements = requirements(host["project"]["dependencies"] + host["dependency-groups"]["artifacts"])
    worker_requirements = requirements(worker["project"]["dependencies"])
    # Artifact authoring belongs in the worker. The two host support extras
    # whose packages consume Pillow need wheel Requires-Dist floors too, without
    # making Pillow a new mandatory host dependency.
    for extra in ("native-desktop", "decisions-openai"):
        assert requirements(host["project"]["optional-dependencies"][extra])["pillow"].specifier == worker_requirements["pillow"].specifier
    for name, (old, fixed, next_major) in FLOORS.items():
        for selected in (host_requirements, worker_requirements):
            spec = selected[name].specifier
            assert old not in spec
            assert ("4.2.0" if name == "filelock" else "12.999") not in spec
            assert fixed in spec
            assert next_major not in spec
        for path in (ROOT / "uv.lock", ROOT / "scripts/work_profile/uv.lock"):
            locked = next(row for row in tomllib.loads(path.read_text())["package"] if row["name"] == name)
            assert Version(locked["version"]) >= Version(fixed)
            assert locked["version"] in host_requirements[name].specifier


ARTIFACT_SMOKE = r'''
import io,json,sys,zipfile
from importlib.metadata import version
from pathlib import Path
from PIL import Image
from docx import Document
from pptx import Presentation
from pptx.util import Inches
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader
root = Path(sys.argv[1]); root.mkdir()
for format in ("PNG", "JPEG", "GIF", "TIFF"):
    encoded = io.BytesIO()
    Image.new("RGB", (40, 20), "navy").save(encoded, format=format)
    encoded.seek(0)
    with Image.open(encoded) as decoded:
        decoded.load()
        resized = decoded.convert("RGB").resize((20, 10), Image.Resampling.LANCZOS)
        resized.save(root / (format + ".png"))
image = root / "PNG.png"
with Image.open(image) as decoded:
    assert decoded.size == (20, 10)
document = Document(); document.add_picture(str(image)); document.save(root / "image.docx")
presentation = Presentation()
presentation.slides.add_slide(presentation.slide_layouts[6]).shapes.add_picture(str(image), Inches(1), Inches(1))
presentation.save(root / "image.pptx")
workbook = Workbook(); workbook.active.add_image(SheetImage(image), "A1"); workbook.save(root / "image.xlsx")
for name, prefix in (("image.docx", "word/media/"), ("image.pptx", "ppt/media/"), ("image.xlsx", "xl/media/")):
    with zipfile.ZipFile(root / name) as archive:
        embedded = [entry for entry in archive.namelist() if entry.startswith(prefix)]
        assert len(embedded) == 1
        assert archive.read(embedded[0]) == image.read_bytes()
canvas = Canvas(str(root / "image.pdf")); canvas.drawImage(ImageReader(str(image)), 10, 10); canvas.save()
reader = PdfReader(root / "image.pdf")
assert len(reader.pages) == 1 and len(reader.pages[0].images) == 1
print(json.dumps({name:version(name) for name in ("Pillow", "filelock", "python-docx", "python-pptx", "openpyxl", "reportlab", "pypdf")}))
'''


async def test_real_worker_resolution_fresh_and_supported_upgrade(tmp_path, monkeypatch):
    wheelhouse = os.environ.get("AMPLIFIER_SECURITY_WHEELS")
    uv = shutil.which("uv")
    if not wheelhouse:
        pytest.skip("Opt-in resolver check requires AMPLIFIER_SECURITY_WHEELS")
    assert uv, "An opted-in resolver check must have an executable uv"
    # Keep real production authoring/version declarations, replacing only the
    # unrelated Git module graph with an offline dependency-focused projection.
    data = tomllib.loads(environments.manifest_path().read_text())
    selected = {"filelock", "pillow", "python-docx", "python-pptx", "openpyxl", "reportlab", "pypdf"}
    specs = [value for value in data["project"]["dependencies"] if Requirement(value).name.lower() in selected]

    def manifest(values):
        return ('[project]\nname="security-worker-projection"\nversion="0.1.0"\n'
                'requires-python=">=3.13"\ndependencies=' + json.dumps(values) + '\n'
                '[tool.uv]\nno-index=true\nfind-links=[' + json.dumps(str(Path(wheelhouse).resolve())) + ']\n').encode()

    fixed = manifest(specs)
    old = manifest([str(Requirement(value).name) + "==" + FLOORS[Requirement(value).name.lower()][0]
                    if Requirement(value).name.lower() in FLOORS else value for value in specs])
    monkeypatch.setenv("UV_NO_INDEX", "true")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    monkeypatch.setattr(environments, "declared_manifest", lambda: fixed)
    monkeypatch.setattr("amplifier_web.app_updates.installed_extras", lambda: [])
    home = tmp_path / "home"

    async def sync(project, *, locked=False):
        from amplifier_web.updates import process
        await process(uv, "sync", *(["--locked"] if locked else []),
                      "--project", str(project), "--python", sys.executable, timeout=120)
        python = project / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        output = subprocess.check_output([str(python), "-I", "-c",
            'import json; from importlib.metadata import version; print(json.dumps({n:version(n) for n in ("Pillow","filelock")}))'],
            text=True, timeout=10)
        return python, json.loads(output)

    fresh = environments.prepare_project(tmp_path / "fresh", content=fixed)
    fresh_python, fresh_versions = await sync(fresh)
    current = environments.prepare_project(home, content=old)
    _, old_versions = await sync(current)
    assert old_versions == {"Pillow": "12.3.0", "filelock": "3.32.7"}
    baseline = environments.receipt_directory(home)
    baseline.mkdir(parents=True)
    (baseline / "runtime.toml").write_bytes(old)
    (baseline / "runtime.lock").write_bytes((current / "uv.lock").read_bytes())
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in baseline.iterdir()}
    old_lock = (current / "uv.lock").read_bytes()
    monkeypatch.setattr("amplifier_web.update_sequence.included_sources", lambda: (set(), []))
    offered = next(row for row in environments.inventory(home) if row["kind"] == "runtime environment")
    assert offered["eligible"] and offered["status"] == "update"

    class Diagnostics:
        async def run(self, _label, function, *args, **kwargs):
            result = function(*args, **kwargs)
            return await result if asyncio.iscoroutine(result) else result

    generation = "a" * 32
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    prepared = await environments.stage(manager, generation, [offered], finalize=False)
    staged_lock = (prepared / "uv.lock").read_bytes()
    _, upgraded_versions = await sync(prepared, locked=True)
    assert (prepared / "uv.lock").read_bytes() == staged_lock
    assert prepared != current
    assert fresh_versions == upgraded_versions == {"Pillow": "13.0.0", "filelock": "4.2.1"}
    from amplifier_web.runtime_qualification import freeze, verify_recorded
    final = await freeze(manager, generation, prepared)
    receipt = environments.receipt_directory(home, generation)
    verify_recorded(final, receipt)
    assert (final / "uv.lock").read_bytes() == (receipt / "runtime.lock").read_bytes()
    upgraded_python, frozen_versions = await sync(final, locked=True)
    assert frozen_versions == upgraded_versions
    # This oracle must reject the old interpreter even though its image APIs work.
    assert all(Version(old_versions[name]) < Version(fixed_version)
               for name, fixed_version in (("Pillow", "13.0.0"), ("filelock", "4.2.1")))
    assert (current / "uv.lock").read_bytes() == old_lock
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in baseline.iterdir()} == before
    for label, python in (("fresh", fresh_python), ("upgrade", upgraded_python)):
        (tmp_path / label).mkdir(exist_ok=True)
        artifact_result = subprocess.run(
            [str(python), "-I", "-c", ARTIFACT_SMOKE, str(tmp_path / label / "artifacts")],
            capture_output=True, text=True, timeout=30)
        assert artifact_result.returncode == 0, artifact_result.stderr
        subprocess.run([str(python), "-I", str(ROOT / "tests/test_filelock_compatibility.py")],
                       check=True, timeout=30)
    # No active pointer or old environment was changed by preparation. Exercise
    # actual launch selection on synthetic pointers only, then restore baseline.
    assert not (home / "updates/active.json").exists()
    from amplifier_web.runtime import RuntimeManager
    pointer = home / "updates/active.json"
    pointer.write_text(json.dumps({"current": generation, "previous": None}))
    selected_command = RuntimeManager()._command(home=home)
    assert Path(RuntimeManager.project_path(selected_command)) == final
    assert "--locked" in selected_command
    pointer.write_text(json.dumps({"current": None, "previous": generation}))
    rollback_command = RuntimeManager()._command(home=home)
    assert Path(RuntimeManager.project_path(rollback_command)) == current
    assert "--locked" in rollback_command
    _, still_old = await sync(current, locked=True)
    assert still_old == old_versions
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in baseline.iterdir()} == before
    if output := os.environ.get("AMPLIFIER_TEST_ARTIFACTS"):
        Path(output, "security-runtime.json").write_text(json.dumps({
            "scope": "Offline dependency-focused worker projection, not full Git graph or live adoption",
            "old": old_versions, "fresh": fresh_versions, "upgrade": upgraded_versions,
            "baselineReceiptSha256": before, "baselinePreserved": True,
            "frozenGraphVerified": True, "generationAndRollbackCommandSelection": True,
            "artifactSmoke": ["PNG", "JPEG", "GIF", "TIFF", "docx", "pptx", "xlsx", "pdf"],
            "filelockSmoke": "fresh and upgraded interpreters; sync contention/nesting and async timeout/cancellation/exception",
        }, indent=2) + "\n")
"""Launcher branching tests use fake uv; real provisioning is an integration check."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(Path(__file__).with_name("launch"), self.root / "scripts/launch")
        (self.root / ".python-version").write_text("3.14.6\n")
        (self.root / "scripts/local.py").write_text("import sys; print(sys.version.split()[0], sys.argv[1])\n")
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "dirname").symlink_to(shutil.which("dirname"))
        (binary / "python3").write_text("#!/bin/sh\nexit 97\n")
        (binary / "python3").chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(binary),
            "FAKE_PYTHON": sys.executable,
            "UV_CALLS": str(self.root / "uv-calls"),
            "INSTALLED": str(self.root / "installed"),
        }
        self.uv = binary / "uv"
        self.uv.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALLS"
if [ "$2" = find ]; then
    if [ "${MISSING:-0}" = 1 ] && [ ! -f "$INSTALLED" ]; then exit 1; fi
    printf '%s\\n' "$FAKE_PYTHON"
else
    if [ "${DOWNLOAD_FAIL:-0}" = 1 ]; then exit 42; fi
    : > "$INSTALLED"
fi
''')
        self.uv.chmod(0o755)

    def invoke(self, action):
        return subprocess.run(
            ["/bin/sh", str(self.root / "scripts/launch"), action],
            cwd="/", env=self.env, text=True, capture_output=True,
        )

    def test_reuses_exact_python_without_path_python_or_download(self):
        result = self.invoke("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.14.6 check", result.stdout)
        calls = (self.root / "uv-calls").read_text()
        self.assertIn("find --no-project --system --no-python-downloads 3.14.6", calls)
        self.assertNotIn("install", calls)

    def test_setup_installs_without_global_command_links(self):
        self.env["MISSING"] = "1"
        result = self.invoke("setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install --no-bin 3.14.6", (self.root / "uv-calls").read_text())

    def test_dev_and_check_never_download(self):
        self.env["MISSING"] = "1"
        for action in ("dev", "check"):
            with self.subTest(action=action):
                result = self.invoke(action)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("./setup", result.stderr)
        self.assertNotIn("install", (self.root / "uv-calls").read_text())

    def test_download_failure_is_actionable(self):
        self.env.update(MISSING="1", DOWNLOAD_FAIL="1")
        result = self.invoke("setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("설치 실패", result.stderr)
        self.assertIn("./setup", result.stderr)

    def test_missing_uv_is_actionable(self):
        self.uv.unlink()
        result = self.invoke("setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uv가 필요", result.stderr)

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local import check, load_environment, port_available, setup, verify_backend_python


class EnvironmentTests(unittest.TestCase):
    def test_values_are_data_not_shell_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text('TOKEN="$(touch /tmp/should-not-execute)"\nWEB_PORT=12345 # comment\nPASSWORD=abc#123\n')
            with patch.dict(os.environ, {}, clear=True):
                env = load_environment(path)
            self.assertEqual(env["TOKEN"], "$(touch /tmp/should-not-execute)")
            self.assertEqual(env["WEB_PORT"], "12345")
            self.assertEqual(env["PASSWORD"], "abc#123")

    def test_process_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text("WEB_PORT=12345\n")
            with patch.dict(os.environ, {"WEB_PORT": "23456"}, clear=True):
                self.assertEqual(load_environment(path)["WEB_PORT"], "23456")

    def test_busy_port_is_preserved(self):
        import socket
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(ValueError):
                port_available(listener.getsockname()[1])
            self.assertGreater(listener.fileno(), 0)


class InterpreterTests(unittest.TestCase):
    def test_backend_interpreter_matches_entrypoint(self):
        with patch("local.subprocess.check_output", return_value=f"{sys.version.split()[0]}\n{os.path.realpath(sys.executable)}\n"):
            verify_backend_python(os.environ.copy())

    def test_wrong_backend_interpreter_is_rejected(self):
        with patch("local.subprocess.check_output", return_value="3.14.5\n/wrong/python\n"):
            with self.assertRaises(ValueError):
                verify_backend_python(os.environ.copy())


class CatalogEntrypointTests(unittest.TestCase):
    def run_entrypoint(self, action):
        example = Path(__file__).resolve().parents[1] / "env.example"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = example.read_text() + "\nWEB_PORT=5274\n"
            (root / "env.example").write_text(example.read_text())
            (root / ".env").write_text(original)
            calls = []

            def capture(command, cwd=None, env=None):
                calls.append((command, env))

            with patch("local.ROOT", root), patch("local.run", side_effect=capture), patch("local.tools"), patch("local.verify_backend_python"):
                action(os.environ.copy())
            self.assertEqual((root / ".env").read_text(), original)
            return calls

    def test_setup_loads_real_catalog_before_application_migrations(self):
        calls = self.run_entrypoint(setup)
        commands = [command for command, _env in calls]
        loaders = [i for i, command in enumerate(commands) if any(str(arg).endswith("bootstrap.py") for arg in command)]
        self.assertEqual(len(loaders), 1, "setup must load the real catalog once")
        migration = next(i for i, command in enumerate(commands) if "alembic" in command)
        self.assertLess(loaders[0], migration)
        docker = next(command for command in commands if command[0] == "docker")
        self.assertIn("--build", docker)

    def test_check_verifies_catalog_without_reimporting(self):
        calls = self.run_entrypoint(check)
        loaders = [(command, env) for command, env in calls if any(str(arg).endswith("bootstrap.py") for arg in command)]
        self.assertEqual(len(loaders), 1, "check must verify the stored catalog")
        self.assertIn("--verify-only", loaders[0][0])
        self.assertIn("CATALOG_DATABASE_URL", loaders[0][1])

    def test_check_cannot_inherit_a_committed_reimport_test(self):
        with patch.dict(os.environ, {"L0_REIMPORT_TESTS": "1"}):
            calls = self.run_entrypoint(check)
        for _command, env in calls:
            self.assertEqual(env.get("L0_REIMPORT_TESTS"), "0")

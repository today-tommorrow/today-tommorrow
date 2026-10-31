import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local import load_environment, port_available, verify_backend_python


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

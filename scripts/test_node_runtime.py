import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from node_runtime import (
    download_verified,
    extract_distribution,
    install_node,
    node_environment,
    usable_node,
)


class NodeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / ".node-version").write_text("26.5.0\n")

    def pair(self, directory):
        directory.mkdir(parents=True)
        node = directory / "node"
        node.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo v26.5.0; else echo 11.17.0; fi\n')
        node.chmod(0o755)
        npm = directory / "npm"
        npm.write_text("// npm fixture\n")
        npm.chmod(0o755)
        return node

    def test_path_pair_is_reused_with_child_only_path(self):
        node = self.pair(self.root / "tools")
        with patch.dict(os.environ, {"PATH": str(node.parent)}, clear=True):
            before = dict(os.environ)
            env = node_environment(self.root, allow_download=False)
            self.assertEqual(env["PATH"].split(os.pathsep)[0], str(node.parent.resolve()))
            self.assertEqual(dict(os.environ), before)

    def test_non_executable_npm_cannot_fall_through(self):
        node = self.pair(self.root / "tools")
        (node.parent / "npm").chmod(0o644)
        self.assertFalse(usable_node(node, "26.5.0"))

    def test_missing_pair_never_downloads_for_dev_or_check(self):
        with (
            patch.dict(os.environ, {"PATH": ""}, clear=True),
            patch("node_runtime.platform.system", return_value="Darwin"),
            patch("node_runtime.platform.machine", return_value="arm64"),
            patch("node_runtime.install_node") as install,
        ):
            with self.assertRaisesRegex(ValueError, "./setup"):
                node_environment(self.root, allow_download=False)
            install.assert_not_called()

    def test_wrong_checksum_is_rejected(self):
        with patch("node_runtime.urllib.request.urlopen", return_value=io.BytesIO(b"not node")):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                download_verified("https://nodejs.org/fixture", self.root / "archive", "0" * 64)

    def archive(self, members):
        path = self.root / "archive.tar.xz"
        with tarfile.open(path, "w:xz") as tar:
            for member, content in members:
                tar.addfile(member, io.BytesIO(content) if content is not None else None)
        return path

    def test_valid_bundled_npm_link_is_allowed(self):
        name = "node-v26.5.0-darwin-arm64"
        member = tarfile.TarInfo(name + "/lib/node_modules/npm/bin/npm-cli.js")
        content = b"// npm\n"
        member.size = len(content)
        link = tarfile.TarInfo(name + "/bin/npm")
        link.type = tarfile.SYMTYPE
        link.linkname = "../lib/node_modules/npm/bin/npm-cli.js"
        archive = self.archive([(member, content), (link, None)])
        extract_distribution(archive, self.root / "stage", name)
        self.assertEqual((self.root / "stage" / name / "bin/npm").read_bytes(), content)

    def test_traversal_and_escaping_links_are_rejected(self):
        name = "node-v26.5.0-darwin-arm64"
        for malicious in (name + "/../../escape", "/absolute"):
            with self.subTest(path=malicious):
                member = tarfile.TarInfo(malicious)
                with self.assertRaises(ValueError):
                    extract_distribution(self.archive([(member, b"")]), self.root / "stage", name)
        link = tarfile.TarInfo(name + "/bin/npm")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        with self.assertRaises(ValueError):
            extract_distribution(self.archive([(link, None)]), self.root / "stage", name)

    def test_checksum_failure_cleans_staging_and_retry_publishes(self):
        name = "node-v26.5.0-darwin-arm64"
        cache = self.root / "cache"
        body = b"archive fixture"
        manifest = f'{hashlib.sha256(body).hexdigest()}  {name}.tar.xz\n'.encode()
        def fetch(url, **kwargs):
            return io.BytesIO(manifest if url.endswith(".txt") else body)
        def extract(archive, stage, distribution):
            self.pair(stage / distribution / "bin")
        with patch("node_runtime.urllib.request.urlopen", side_effect=fetch):
            with patch("node_runtime.download_verified", side_effect=ValueError("SHA256")):
                with self.assertRaises(ValueError):
                    install_node(cache, "26.5.0", name)
            self.assertEqual([p.name for p in cache.iterdir()], ["install.lock"])
            with patch("node_runtime.extract_distribution", side_effect=extract):
                binary = install_node(cache, "26.5.0", name)
            self.assertTrue(usable_node(binary, "26.5.0"))

    def test_missing_official_build_has_no_fallback(self):
        with patch("node_runtime.urllib.request.urlopen", return_value=io.BytesIO(b"")):
            with self.assertRaisesRegex(ValueError, "공식 배포"):
                install_node(self.root / "cache", "26.5.0", "node-v26.5.0-darwin-x64")

    def test_invalid_old_cache_is_preserved_when_replaced(self):
        name = "node-v26.5.0-darwin-arm64"
        cache = self.root / "cache"
        target = cache / name
        target.mkdir(parents=True)
        (target / "sentinel").write_text("preserve")
        body = b"archive fixture"
        manifest = f'{hashlib.sha256(body).hexdigest()}  {name}.tar.xz\n'.encode()
        def fetch(url, **kwargs):
            return io.BytesIO(manifest if url.endswith(".txt") else body)
        def extract(archive, stage, distribution):
            self.pair(stage / distribution / "bin")
        with patch("node_runtime.urllib.request.urlopen", side_effect=fetch), patch("node_runtime.extract_distribution", side_effect=extract):
            binary = install_node(cache, "26.5.0", name)
        self.assertTrue(usable_node(binary, "26.5.0"))
        backups = list(cache.glob(name + ".previous-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "sentinel").read_text(), "preserve")

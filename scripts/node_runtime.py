"""Select one pinned Node/npm pair; setup alone may provision an official archive."""

import fcntl
import hashlib
import os
import platform
import posixpath
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


def usable_node(binary, pin):
    binary = binary.resolve()
    npm = binary.parent / "npm"
    if not binary.is_file() or not npm.is_file() or not os.access(npm, os.X_OK):
        return False
    try:
        version = subprocess.check_output([str(binary), "--version"], stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
        npm_version = subprocess.check_output([str(binary), str(npm.resolve()), "--version"], stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
        return version == f"v{pin}" and bool(re.fullmatch(r"\d+\.\d+\.\d+", npm_version))
    except (OSError, subprocess.SubprocessError):
        return False


def distribution(pin):
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
    if platform.system() != "Darwin" or arch is None:
        raise ValueError("Node 자동 준비는 macOS arm64/x64만 지원합니다. 지원 환경과 .node-version을 확인해주세요.")
    return f"node-v{pin}-darwin-{arch}"


def download_verified(url, destination, expected):
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=30) as response, destination.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 256 * 1024 * 1024:
                raise ValueError("Node archive 크기 제한을 초과했습니다.")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("Node SHA256 검증에 실패했습니다. 설치하지 않았습니다. 네트워크를 확인한 뒤 ./setup을 재시도해주세요.")


def extract_distribution(archive, destination, name):
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != name:
                raise ValueError("안전하지 않은 Node archive 경로입니다.")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("지원하지 않는 Node archive 항목입니다.")
            if member.issym() or member.islnk():
                target = member.linkname if member.islnk() else posixpath.join(str(path.parent), member.linkname)
                normalized = PurePosixPath(posixpath.normpath(target))
                if normalized.is_absolute() or not normalized.parts or normalized.parts[0] != name:
                    raise ValueError("Node archive 링크가 설치 경로를 벗어납니다.")
        # data filtering also validates resolved link paths while allowing bundled npm.
        tar.extractall(destination, members=members, filter="data")


def install_node(cache, pin, name):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    with (cache / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if usable_node(target / "bin/node", pin):
            return target / "bin/node"
        base = f"https://nodejs.org/dist/v{pin}/"
        filename = name + ".tar.xz"
        with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=30) as response:
            manifest = response.read(2 * 1024 * 1024).decode("ascii")
        checksums = {fields[1].lstrip("*"): fields[0] for line in manifest.splitlines() if len(fields := line.split()) == 2}
        expected = checksums.get(filename, "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"공식 배포에 {filename}이 없습니다. 다른 버전·도구로 자동 대체하지 않습니다.")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=cache) as temporary:
            stage = Path(temporary)
            archive = stage / filename
            print(f"Node {pin} 준비 중 · {filename} (공식 SHA256 검증)", flush=True)
            download_verified(base + filename, archive, expected)
            extract_distribution(archive, stage, name)
            candidate = stage / name
            if not usable_node(candidate / "bin/node", pin):
                raise ValueError("설치한 Node/npm을 실행할 수 없습니다. ./setup을 다시 실행해주세요.")
            # Retain any damaged prior cache instead of deleting user data.
            backup = None
            if target.exists() or target.is_symlink():
                backup = cache / (name + ".previous-" + stage.name)
                target.rename(backup)
            try:
                candidate.rename(target)
            except OSError:
                if backup is not None:
                    backup.rename(target)
                raise
        return target / "bin/node"


def node_environment(root, allow_download):
    pin = (root / ".node-version").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", pin):
        raise ValueError(".node-version에 정확한 숫자 버전이 필요합니다.")
    for directory in os.get_exec_path():
        binary = Path(directory) / "node"
        if usable_node(binary, pin):
            break
    else:
        name = distribution(pin)
        cache = root / ".local" / "node"
        binary = cache / name / "bin/node"
        if not usable_node(binary, pin):
            if not allow_download:
                raise ValueError(f"사용 가능한 Node {pin}/npm이 없습니다. 먼저 ./setup을 실행해주세요.")
            try:
                binary = install_node(cache, pin, name)
            except (OSError, tarfile.TarError) as error:
                raise ValueError("Node 준비에 실패했습니다. 네트워크·저장 공간을 확인한 뒤 ./setup을 다시 실행해주세요.") from error
    selected = str(binary.resolve().parent)
    print(f"Node {pin} · {selected} (동일 설치의 npm 사용)", flush=True)
    return {**os.environ, "PATH": selected + os.pathsep + os.environ.get("PATH", "")}

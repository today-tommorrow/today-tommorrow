"""Local development entry points; never execute the contents of .env."""

import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from node_runtime import node_environment

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd=ROOT, env=None):
    print(f"→ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def load_environment(path):
    """Support KEY=value, quoted values and comments; no expansion or shell code."""
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f".env {number}행: KEY=value 형식이 필요합니다.")
        quote = None
        escaped = False
        for index, character in enumerate(raw):
            if escaped:
                escaped = False
            elif character == "\\" and quote != "'":
                escaped = True
            elif character in ("'", '"'):
                if quote is None:
                    quote = character
                elif quote == character:
                    quote = None
            elif character == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
                raw = raw[:index]
                break
        tokens = shlex.split(raw, comments=False)
        if len(tokens) > 1:
            raise ValueError(f".env {number}행: 공백이 있는 값은 따옴표로 감싸주세요.")
        values[key] = tokens[0] if tokens else ""
    return {**values, **os.environ}


def require_python():
    expected = (ROOT / ".python-version").read_text().strip()
    if sys.version.split()[0] != expected:
        raise ValueError(f"Python {expected}가 필요합니다. 저장소의 ./setup을 실행해주세요.")


def verify_backend_python(env):
    interpreter = ROOT / "backend" / ".venv" / "bin" / "python"
    actual = subprocess.check_output(
        [str(interpreter), "-c", "import os,sys; print(sys.version.split()[0]); print(os.path.realpath(sys.executable))"],
        env=env,
        text=True,
    ).splitlines()
    expected = [sys.version.split()[0], os.path.realpath(sys.executable)]
    if actual != expected:
        raise ValueError("backend/.venv의 Python이 진입점과 다릅니다. ./setup을 다시 실행해주세요.")
    print(f"backend Python {actual[0]} · {actual[1]}", flush=True)


def tools(env):
    for name in ("git", "node", "npm", "uv", "docker"):
        if not shutil.which(name, path=env["PATH"]):
            raise ValueError(f"{name}를 찾을 수 없습니다. README의 공통 준비를 확인해주세요.")
    node = subprocess.check_output(["node", "--version"], env=env, text=True).strip().lstrip("v")
    if node != (ROOT / ".node-version").read_text().strip():
        raise ValueError("Node 버전이 다릅니다. .node-version에 기록된 버전으로 전환해주세요.")
    for name in ("git", "npm", "uv"):
        run([name, "--version"], env=env)
    compose = subprocess.check_output(["docker", "compose", "version", "--short"], env=env, text=True).strip()
    if int(compose.split(".")[0].lstrip("v")) < 2:
        raise ValueError("Docker Compose v2 이상이 필요합니다. Docker 설치를 업데이트해주세요.")
    print(f"Docker Compose {compose}", flush=True)
    try:
        subprocess.run(["docker", "info"], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        raise ValueError("Docker 엔진에 연결할 수 없습니다. 로컬 엔진을 시작한 뒤 docker info를 확인해주세요.") from None


def setup(node_env):
    tools(node_env)
    path = ROOT / ".env"
    try:
        with path.open("x") as output:
            output.write((ROOT / "env.example").read_text())
    except FileExistsError:
        pass
    env = {**load_environment(path), **node_env}
    run(["npm", "ci"], ROOT / "web", env)
    run(["uv", "sync", "--python", sys.executable, "--locked", "--dev"], ROOT / "backend", env)
    verify_backend_python(env)
    run(["uv", "sync", "--project", str(ROOT / "tools/catalog"), "--python", sys.executable, "--locked"], env=env)
    # Pass parsed values via the process environment; Compose never evaluates .env.
    run(["docker", "compose", "--env-file", os.devnull, "up", "-d", "--build", "--wait", "db"], env=env)
    catalog(env)
    run(["uv", "run", "--python", sys.executable, "--locked", "alembic", "upgrade", "head"], ROOT / "backend", env)
    print("준비 완료. ./dev로 시작하고 ./check로 검사하세요.")


def catalog(env, verify_only=False):
    command = [
        "uv", "run", "--project", str(ROOT / "tools/catalog"), "--python", sys.executable,
        "--locked", "python", str(ROOT / "tools/catalog/bootstrap.py"),
        "--report", str(ROOT / ".local/catalog" / ("verify.json" if verify_only else "bootstrap.json")),
    ]
    if verify_only:
        command.append("--verify-only")
    run(command, env=env)


def port_available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise ValueError(f"포트 {port}가 사용 중입니다. 기존 프로세스는 종료하지 않습니다. .env를 조정해주세요.") from None


def stop_children(children):
    # Each child owns a new POSIX process group, including npm/uv descendants.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for child in children:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def dev(node_env):
    env = {**load_environment(ROOT / ".env"), **node_env}
    target = urlparse(env.get("API_TARGET", "http://127.0.0.1:8000"))
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.path not in ("", "/") or target.query or target.fragment or target.username:
        raise ValueError("API_TARGET은 http://127.0.0.1:<포트> 형식이어야 합니다.")
    api_port = target.port if target.port is not None else 80
    web_port = int(env.get("WEB_PORT", "5173"))
    if not 1 <= web_port <= 65535 or not 1 <= api_port <= 65535 or api_port == web_port:
        raise ValueError("API와 웹에는 서로 다른 유효한 포트가 필요합니다.")
    for port in (api_port, web_port):
        port_available(port)
    print(f"웹 http://127.0.0.1:{web_port} · API {target.geturl()} · Ctrl-C로 두 서버 종료", flush=True)
    children = []
    interrupted = 0

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = signum

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        commands = [
            (["uv", "run", "--python", sys.executable, "--locked", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)], ROOT / "backend"),
            (["npm", "run", "dev"], ROOT / "web"),
        ]
        for command, cwd in commands:
            children.append(subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True))
        while not interrupted:
            for child in children:
                status = child.poll()
                if status is not None:
                    print("서버가 종료되어 함께 실행한 서버도 종료합니다.", flush=True)
                    return status if status > 0 else (128 - status if status < 0 else 1)
            time.sleep(0.1)
        return 128 + interrupted
    finally:
        stop_children(children)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def check(node_env):
    # UI fixtures are isolated; integration checks read the prepared local catalog.
    env = {
        **load_environment(ROOT / ".env"), **node_env,
        "CI": "1", "L0_DATABASE_TESTS": "1", "L0_REIMPORT_TESTS": "0",
    }
    catalog(env, verify_only=True)
    for command in (["npm", "run", "typecheck"], ["npm", "run", "lint"], ["npm", "run", "format:check"], ["npm", "test"]):
        run(command, ROOT / "web", env)
    run(["uv", "run", "--python", sys.executable, "--locked", "ruff", "check", "--config", "pyproject.toml", ".", "../scripts", "../tools/catalog"], ROOT / "backend", env)
    run(["uv", "run", "--python", sys.executable, "--locked", "python", "-m", "unittest", "discover", "-s", "tests"], ROOT / "backend", env)
    run([sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"], env=env)
    run([
        "uv", "run", "--project", str(ROOT / "tools/catalog"), "--python", sys.executable,
        "--locked", "python", "-m", "unittest", "discover", "-s", "tools/catalog/tests",
    ], env=env)
    run([
        "uv", "run", "--project", str(ROOT / "tools/catalog"), "--python", sys.executable,
        "--locked", "python", "-m", "unittest", "discover", "-s", "tools/catalog/integration",
    ], env=env)
    run(["npm", "run", "build"], ROOT / "web", env)


if __name__ == "__main__":
    try:
        require_python()
        action = {"setup": setup, "dev": dev, "check": check}[sys.argv[1]]
        node_env = node_environment(ROOT, allow_download=sys.argv[1] == "setup")
        sys.exit(action(node_env) or 0)
    except (ValueError, OSError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as error:
        print(f"명령 실패 (종료 코드 {error.returncode}). 위 출력을 확인해주세요.", file=sys.stderr)
        sys.exit(error.returncode if error.returncode > 0 else 1)

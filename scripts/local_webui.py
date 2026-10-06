#!/usr/bin/env python3
"""Run upstream WebUI with project-local tools, identity and storage.

No model credentials are read or copied. This wrapper deliberately has no
fallback to globally installed tools, old workspaces or authentication bypass.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
BUN = ROOT / '.tools/bun-darwin-aarch64/bun'
CORE = ROOT / '.tools/cargo-target/debug/aioncore'
WEBUI = ROOT / 'AionUi/scripts/webui.ts'
PORT = 25849
URL = f'http://sales-agent.localhost:{PORT}'


def isolated_env(root: Path = ROOT) -> dict[str, str]:
    """Allowlist environment instead of inheriting provider keys or old paths."""
    home = root / '.runtime/home'
    env = {
        'HOME': str(home),
        'SHELL': '/bin/bash',
        'PATH': f'{root}/.tools/bun-darwin-aarch64:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'LANG': 'en_US.UTF-8',
        'TMPDIR': str(root / '.runtime/tmp'),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'XDG_CACHE_HOME': str(home / '.cache'),
        'XDG_DATA_HOME': str(home / '.local/share'),
        'AIONUI_DATA_DIR': str(root / '.runtime/aionui'),
        'AIONUI_WORK_DIR': str(root / '.runtime/aionui'),
        'AIONUI_CACHE_DIR': str(root / '.runtime/aionui'),
        'AIONUI_LOG_DIR': str(root / '.runtime/logs/core'),
        'AIONUI_BACKEND_BIN': str(root / '.tools/cargo-target/debug/aioncore'),
        'AIONUI_LOG_LEVEL': 'info',
        'AIONUI_OPEN_BROWSER': '0',
        'AIONUI_ALLOW_REMOTE': '0',
        'AIONUI_PORT': str(PORT),
        'BUN_INSTALL_CACHE_DIR': str(root / '.tools/bun-cache'),
        'npm_config_cache': str(root / '.tools/npm-cache'),
        'NO_COLOR': '1',
    }
    meeting_config = root / '.runtime/meeting-service.json'
    if meeting_config.is_file():
        token = json.loads(meeting_config.read_text()).get('token', '')
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            raise RuntimeError('Invalid project meeting service configuration; value not displayed.')
        env['AIONUI_MEETING_TOKEN'] = token
        env['AIONUI_MEETING_PORT'] = '8049'
    return env


def private_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def process_identity(pid: int) -> str:
    result = subprocess.run(
        ['/bin/ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ''


def owned_process(state: dict) -> bool:
    identity = process_identity(state['pid'])
    return bool(identity and identity == state['identity'] and str(WEBUI) in identity and str(BUN) in identity)


def read_state() -> dict:
    path = RUNTIME / 'webui-process.json'
    return json.loads(path.read_text()) if path.exists() else {}


def ensure_free_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Match the server's restart semantics without mistaking TIME_WAIT for
        # a live listener. SO_REUSEPORT is intentionally not enabled.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'Port {port} is occupied; no existing service was stopped.') from None


def bootstrap(env: dict[str, str]) -> None:
    access = RUNTIME / 'local-access.json'
    database = RUNTIME / 'aionui/aionui-backend.db'
    if database.exists():
        if not access.exists():
            raise RuntimeError('Existing project database has no local-access file; refusing to reset credentials.')
        return
    if not access.exists():
        private_json(access, {'username': 'admin', 'password': 'Aa9!' + secrets.token_urlsafe(24)})
    password = json.loads(access.read_text())['password']
    result = subprocess.run(
        [str(CORE), '--data-dir', env['AIONUI_DATA_DIR'], 'user', 'set-password', '--password-stdin'],
        input=password + '\n', text=True, capture_output=True, env=env, cwd=ROOT,
        timeout=120, check=False,
    )
    # Never echo subprocess output: future upstream versions may change it.
    if result.returncode:
        raise RuntimeError(f'Local-account bootstrap failed (exit {result.returncode}); credentials not displayed.')


def ready() -> bool:
    # Bypass global proxy configuration; never attach credentials here.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{PORT}/api/auth/status', timeout=2) as response:
            body = json.load(response)
            return response.status == 200 and body.get('needs_setup') is False
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start() -> None:
    state = read_state()
    if state and owned_process(state):
        print(f'Already running: {URL}')
        return
    ensure_free_port(PORT)
    for path in [BUN, CORE, ROOT / 'AionUi/out/renderer/index.html']:
        if not path.is_file():
            raise RuntimeError(f'Required build artifact is missing: {path.relative_to(ROOT)}')
    env = isolated_env()
    for path in [RUNTIME / 'home', RUNTIME / 'tmp', RUNTIME / 'logs/core', RUNTIME / 'aionui']:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    bootstrap(env)
    with open(RUNTIME / 'logs/webui.log', 'ab', opener=lambda p, f: os.open(p, f, 0o600)) as log:
        process = subprocess.Popen(
            [str(BUN), str(WEBUI), '--no-build', '--no-open', '--port', str(PORT)],
            cwd=ROOT / 'AionUi', env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    identity = process_identity(process.pid)
    state = {'pid': process.pid, 'identity': identity, 'url': URL}
    private_json(RUNTIME / 'webui-process.json', state)
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'WebUI exited ({process.returncode}); inspect the private project log.')
        if ready():
            print(f'Running (not a business acceptance result): {URL}')
            print('Local login is stored in .runtime/local-access.json (not printed).')
            return
        time.sleep(0.5)
    # Keep the recorded PID so a slow startup can still be diagnosed/stopped.
    raise RuntimeError('Startup readiness timed out; project PID retained for diagnosis.')


def stop() -> None:
    state = read_state()
    if not state or not owned_process(state):
        print('No matching project WebUI process; nothing was stopped.')
        return
    os.kill(state['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not owned_process(state):
            print('Project WebUI stopped. Other services were not touched.')
            return
        time.sleep(0.2)
    raise RuntimeError('Graceful shutdown timed out; no unrelated process was killed.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True, mode=0o700)
    with open(RUNTIME / 'launcher.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'start':
            start()
        elif args.action == 'stop':
            stop()
        else:
            state = read_state()
            running = bool(state and owned_process(state))
            print(json.dumps({'running': running, 'ready': ready() if running else False, 'url': URL if running else None}))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, BlockingIOError) as exc:
        raise SystemExit(str(exc)) from None

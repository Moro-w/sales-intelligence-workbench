#!/usr/bin/env python3
"""Manage this project's Tingji text-only service; never install/load audio models."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import urllib.request

from local_webui import ROOT, RUNTIME, ensure_free_port, isolated_env, private_json, process_identity

PYTHON = ROOT / '.tools/tingji-venv/bin/python'
PORT = 8049
STATE = RUNTIME / 'meeting-process.json'
CONFIG = RUNTIME / 'meeting-service.json'


def owned(state):
    pid = state.get('pid')
    if type(pid) is not int or pid <= 0:
        return False
    identity = process_identity(pid)
    return bool(identity and identity == state.get('identity') and str(PYTHON) in identity and 'app.text_main:create_app' in identity)


def ready():
    if not CONFIG.exists():
        return False
    try:
        token = json.loads(CONFIG.read_text())['token']
        req = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/status', headers={
            'X-Tingji-Service-Token': token, 'X-Tingji-User-Id': 'health-probe'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            return response.status == 200 and json.load(response).get('mode') == 'text-only'
    except (OSError, ValueError, KeyError):
        return False


def start():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if owned(state):
        print('Project meeting service already running.')
        return
    ensure_free_port(PORT)
    if not PYTHON.exists():
        raise RuntimeError('Project text-only environment is missing.')
    if not CONFIG.exists():
        private_json(CONFIG, {'token': secrets.token_hex(32)})
    env = isolated_env()
    env['TINGJI_SERVICE_TOKEN'] = env['AIONUI_MEETING_TOKEN']
    env['TINGJI_DATA_DIR'] = str(RUNTIME / 'tingji')
    env['TINGJI_MODEL_GRANT'] = str(RUNTIME / 'meeting-model-grant.json')
    (RUNTIME / 'logs').mkdir(exist_ok=True)
    with open(RUNTIME / 'logs/meeting-service.log', 'ab') as log:
        process = subprocess.Popen([str(PYTHON), '-m', 'uvicorn', 'app.text_main:create_app', '--factory',
            '--host', '127.0.0.1', '--port', str(PORT), '--no-access-log'], cwd=ROOT / 'Tingji', env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    private_json(STATE, {'pid': process.pid, 'identity': process_identity(process.pid)})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Meeting service exited; inspect private project log.')
        if ready():
            print('Project Tingji text-only service ready on loopback port 8049; no audio models loaded.')
            return
        time.sleep(.25)
    raise RuntimeError('Meeting startup timed out; process identity retained.')


def stop():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not owned(state):
        print('No matching project meeting process; nothing stopped.')
        return
    os.kill(state['pid'], signal.SIGTERM)
    # An already authorized in-flight model call has a 120s timeout. Let it settle
    # its budget/checkpoint, then mark the task interrupted; do not kill unrelated PIDs.
    deadline = time.monotonic() + 135
    while time.monotonic() < deadline:
        if not owned(state):
            print('Project meeting service stopped; stored meetings retained.')
            return
        time.sleep(.1)
    raise RuntimeError('Meeting service did not stop; no unrelated process killed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True)
    with open(RUNTIME / 'meeting-launcher.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'start': start()
        elif args.action == 'stop': stop()
        else:
            state = json.loads(STATE.read_text()) if STATE.exists() else {}
            print(json.dumps({'running': owned(state), 'ready': ready(), 'port': PORT}))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, BlockingIOError) as exc:
        raise SystemExit(str(exc)) from None

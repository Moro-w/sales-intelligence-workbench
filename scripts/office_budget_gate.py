#!/usr/bin/env python3
"""Temporary loopback-only DeepSeek gate for the authorized office acceptance run.

No prompts, response text, credentials or raw exceptions are logged. The ledger
reserves before network I/O; failures keep their full reservation. This is test
infrastructure, not a replacement for the application's normal provider path.
"""
from __future__ import annotations
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
PORT = 25850
MODEL = 'deepseek-flash'
MAX_REQUESTS = 12
MAX_INPUT = 150_000
MAX_OUTPUT = 12_000
PER_OUTPUT = 1000
# Official CN peak, cache-miss price; deliberately ignore discounts/cache hits.
INPUT_MICROYUAN = 2
OUTPUT_MICROYUAN = 8
MAX_MICROYUAN = 2_000_000


def save_private(path, value):
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


class Rejected(Exception):
    pass


class Budget:
    def __init__(self, path):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'requests': [], 'halted': False}

    def totals(self):
        rows = self.state['requests']
        inp = sum(r['charged_input'] for r in rows)
        out = sum(r['charged_output'] for r in rows)
        return inp, out, inp * INPUT_MICROYUAN + out * OUTPUT_MICROYUAN

    def reserve(self, body):
        if self.state['halted'] or len(self.state['requests']) >= MAX_REQUESTS:
            raise Rejected('Authorized request limit reached')
        if body.get('model') != MODEL:
            raise Rejected('Only the authorized model is allowed')
        if not isinstance(body.get('messages'), list) or not body['messages']:
            raise Rejected('Messages required')
        # This run is text-only. Do not forward image/audio URLs or base64 data.
        for msg in body['messages']:
            if not isinstance(msg, dict) or not isinstance(msg.get('content', ''), (str, type(None))):
                raise Rejected('Text-only messages required')
        body = dict(body)
        body.pop('max_completion_tokens', None)
        body['max_tokens'] = PER_OUTPUT
        body['thinking'] = {'type': 'disabled'}
        if body.get('stream'):
            body['stream_options'] = {'include_usage': True}
        payload = json.dumps(body, ensure_ascii=True, separators=(',', ':')).encode()
        # Conservative byte upper estimate + generous message/tool framing reserve.
        # Provider usage is checked before releasing any reservation.
        reserve_in = len(payload) + 4096 + 128 * (len(body['messages']) + len(body.get('tools', [])))
        inp, out, cost = self.totals()
        if inp + reserve_in > MAX_INPUT or out + PER_OUTPUT > MAX_OUTPUT or cost + reserve_in * INPUT_MICROYUAN + PER_OUTPUT * OUTPUT_MICROYUAN > MAX_MICROYUAN:
            raise Rejected('Authorized token or monetary budget reached')
        row = {'number': len(self.state['requests']) + 1, 'status': 'reserved', 'reserved_input': reserve_in,
               'charged_input': reserve_in, 'charged_output': PER_OUTPUT,
               'request_sha256': hashlib.sha256(payload).hexdigest()}
        self.state['requests'].append(row)
        save_private(self.path, self.state)
        return row, payload

    def settle(self, row, usage, status, completed):
        row['status'] = status
        if completed and isinstance(usage, dict):
            inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if type(inp) is int and type(out) is int and inp >= 0 and out >= 0:
                row['usage'] = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens') if type(usage.get(k)) is int}
                if inp <= row['reserved_input'] and out <= PER_OUTPUT:
                    row['charged_input'], row['charged_output'] = inp, out
                else:
                    self.state['halted'] = True
                    row['status'] = 'usage_bound_violation'
                    row['charged_input'] = max(inp, row['charged_input'])
                    row['charged_output'] = max(out, row['charged_output'])
        self.state['conservative_cny'] = self.totals()[2] / 1_000_000
        save_private(self.path, self.state)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_handler(config, budget):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(90)

        def error(self, code, text):
            payload = json.dumps({'error': {'message': text, 'type': 'office_budget_gate'}}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                return self.error(404, 'Endpoint not allowed')
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + config['local_token']):
                return self.error(401, 'Unauthorized')
            try:
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                    raise Rejected('Encoded bodies not allowed')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 150_000:
                    raise Rejected('Body size outside allowed range')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise Rejected('Incomplete request')
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise Rejected('Object body required')
                row, payload = budget.reserve(body)
            except (ValueError, Rejected, socket.timeout) as exc:
                return self.error(400, str(exc) if isinstance(exc, Rejected) else 'Invalid request')
            usage, completed, status = None, False, 'network_failure'
            try:
                req = urllib.request.Request('https://api.deepseek.com/chat/completions', data=payload,
                    headers={'Authorization': 'Bearer ' + config['upstream_key'], 'Content-Type': 'application/json'})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                with opener.open(req, timeout=90) as response:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    status = 'ok'
                    if body.get('stream'):
                        for line in response:
                            if line.startswith(b'data: '):
                                data = line[6:].strip()
                                if data == b'[DONE]':
                                    completed = True
                                else:
                                    chunk = json.loads(data)
                                    if chunk.get('usage'):
                                        usage = chunk['usage']
                                    if chunk.get('error'):
                                        status = 'upstream_stream_error'
                                        break
                            self.wfile.write(line)
                            self.wfile.flush()
                    else:
                        data = response.read(2_000_000)
                        usage = json.loads(data).get('usage')
                        completed = True
                        self.wfile.write(data)
            except urllib.error.HTTPError as exc:
                status = 'upstream_http_' + str(exc.code)
                self.error(exc.code, 'Upstream rejected request; details withheld')
            except Exception:
                # Never expose exception strings (URLs/headers/payloads may be embedded).
                status = 'transport_interrupted'
            finally:
                self.close_connection = True
                budget.settle(row, usage, status, completed)
    return Handler


def main():
    os.umask(0o077)
    with open(RUNTIME / 'office-budget.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads((RUNTIME / 'office-budget-private.json').read_text())
        budget = Budget(RUNTIME / 'evidence/office-budget-ledger.json')
        server = HTTPServer(('127.0.0.1', PORT), make_handler(config, budget))
        print('Office budget gate ready on loopback port 25850', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()

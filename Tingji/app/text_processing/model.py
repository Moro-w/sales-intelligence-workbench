"""Explicit batch authorization and durable preflight budget. No implicit retries."""
import fcntl
import json
import os
from pathlib import Path
import time
import httpx
from app.text_storage import TextError
from .contracts import fingerprint

MODEL = 'deepseek-flash'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
# Conservative official peak/cache-miss prices; batch approval must recheck them.
INPUT_MICROYUAN = 2
OUTPUT_MICROYUAN = 8
OUTPUT_LIMIT = 8192
# A whole-meeting board is one structured response; cleaning stays per chunk.
STAGE_OUTPUT_LIMIT = {'board_draft': 16384, 'board_merge': 16384}


def strict_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError('Non-JSON number')
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, ensure_ascii=False)
        out.flush(); os.fsync(out.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class BudgetedModel:
    def __init__(self, grant_path=None, transport=None):
        self.path = Path(grant_path) if grant_path else None
        self.transport = transport  # test injection only, never configurable by HTTP

    def _grant(self):
        if not self.path or not self.path.is_file():
            raise TextError('MODEL_NOT_AUTHORIZED', 'AI 处理尚未开通，当前只保存原稿；开通后会自动清洗并生成看板。', 409)
        try:
            g = json.loads(self.path.read_text())
            if isinstance(g, dict) and g.get('enabled') is False:
                raise TextError('MODEL_NOT_AUTHORIZED', 'AI 处理已暂停，原稿及已保存内容仍可查看。', 409)
            assert g['enabled'] is True and g['model'] == MODEL
            assert isinstance(g['batch_id'], str) and len(g['batch_id']) >= 8
            assert isinstance(g['api_key'], str) and g['api_key']
            assert isinstance(g['owner'], str) and g['owner']
            assert isinstance(g['source_hashes'], list) and g['source_hashes']
            assert all(isinstance(s, str) and len(s) == 64 for s in g['source_hashes'])
            assert g['expires_at'] > time.time()
            if 'processing_protocol' in g:
                assert g['processing_protocol'] == 'sales-board-first-v1'
                assert isinstance(g.get('products'), list) and 1 <= len(g['products']) <= 2
                assert all(p in ('board','clean') for p in g['products'])
                assert len(set(g['products'])) == len(g['products'])
            for k in ('max_requests', 'max_input', 'max_output', 'max_microyuan'):
                assert type(g[k]) is int and g[k] > 0
            assert g['input_price_microyuan'] == INPUT_MICROYUAN and g['output_price_microyuan'] == OUTPUT_MICROYUAN
            assert (self.path.stat().st_mode & 0o077) == 0
        except (AssertionError, ValueError, KeyError, TypeError, OSError):
            raise TextError('MODEL_NOT_AUTHORIZED', 'AI 处理暂不可用，不会自动调用。', 409) from None
        return g

    def status(self, owner=None, source_hash=None):
        try:
            g = self._grant()
            if owner is not None and g['owner'] != owner:
                raise TextError('MATERIAL_NOT_AUTHORIZED', '当前账号未开通 AI 处理。', 403)
            if source_hash is not None and source_hash not in g['source_hashes']:
                raise TextError('MATERIAL_NOT_AUTHORIZED', '这份会议稿尚未开通 AI 处理，原稿已保存。', 403)
            ledger = self._ledger(g)
            if ledger['halted'] or len(ledger['requests']) >= g['max_requests']:
                raise TextError('BUDGET_EXHAUSTED', 'AI 处理额度已用完，不会继续计费。', 409)
            return {'enabled': True, 'reason': None, 'batch_id': g['batch_id']}
        except TextError as exc:
            return {'enabled': False, 'reason': exc.message, 'code': exc.code}

    def product_status(self, owner, source_hash, product, protocol):
        gate = self.status(owner, source_hash)
        if not gate['enabled']: return gate
        grant = self._grant()
        if grant.get('processing_protocol') != protocol or product not in grant.get('products', []):
            return {'enabled':False,'code':'PRODUCT_NOT_AUTHORIZED','reason':'当前未开通这类处理。'}
        return gate

    def _ledger(self, grant):
        path = self.path.with_name('meeting-model-ledger.json')
        if not path.exists(): return {'batch_id': grant['batch_id'], 'halted': False, 'requests': []}
        try:
            value = json.loads(path.read_text())
            assert value['batch_id'] == grant['batch_id']
            assert isinstance(value['requests'], list) and type(value['halted']) is bool
            assert all(isinstance(r, dict) and all(type(r[k]) is int and r[k] >= 0 for k in ('charged_input', 'charged_output')) for r in value['requests'])
            return value
        except (ValueError, KeyError, AssertionError, TypeError, OSError):
            raise TextError('BUDGET_CLOSED', '预算账本与授权不一致，已停止；不能自动重置额度。', 409) from None

    def call(self, messages, owner, source_hash, stage, chunk):
        product = {'board_draft':'board','board_merge':'board','clean_draft':'clean'}.get(stage)
        gate = self.product_status(owner, source_hash, product, 'sales-board-first-v1') if product else self.status(owner, source_hash)
        if not gate['enabled']: raise TextError(gate['code'], gate['reason'], 409)
        limit = STAGE_OUTPUT_LIMIT.get(stage, OUTPUT_LIMIT)
        payload = {'model': MODEL, 'messages': messages, 'temperature': 0, 'stream': False,
                   'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'}, 'max_tokens': limit}
        wire = json.dumps(payload, ensure_ascii=False).encode()
        reserve = len(wire) + 4096 + 128 * len(messages)  # byte upper estimate + framing allowance
        ledger_path = self.path.with_name('meeting-model-ledger.json')
        with open(self.path.with_name('meeting-model-budget.lock'), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            grant = self._grant()
            # Recheck materials under lock; the operator may revoke the batch while queued.
            if grant.get('processing_protocol') == 'sales-board-first-v1' and product is None:
                raise TextError('PRODUCT_NOT_AUTHORIZED', '不能用看板优先批次运行旧版流水线。', 409)
            if product and (grant.get('processing_protocol') != 'sales-board-first-v1' or product not in grant.get('products', [])):
                raise TextError('PRODUCT_NOT_AUTHORIZED', '当前授权不包含此产物。', 409)
            if grant['owner'] != owner or source_hash not in grant['source_hashes']:
                raise TextError('MATERIAL_NOT_AUTHORIZED', '材料授权已变化，已停止。', 403)
            ledger = self._ledger(grant)
            inp = sum(r['charged_input'] for r in ledger['requests'])
            out = sum(r['charged_output'] for r in ledger['requests'])
            if (ledger['halted'] or len(ledger['requests']) >= grant['max_requests'] or
                inp + reserve > grant['max_input'] or out + limit > grant['max_output'] or
                (inp + reserve) * INPUT_MICROYUAN + (out + limit) * OUTPUT_MICROYUAN > grant['max_microyuan']):
                raise TextError('BUDGET_EXHAUSTED', 'AI 处理额度不足，已停止，不会自动扩额。', 409)
            row = {'number': len(ledger['requests']) + 1, 'stage': stage, 'chunk': chunk,
                   'request_sha256': fingerprint(payload), 'source_sha256': source_hash,
                   'reserved_input': reserve, 'charged_input': reserve, 'charged_output': limit,
                   'status': 'reserved', 'started_at': time.time()}
            ledger['requests'].append(row)
            atomic_json(ledger_path, ledger)  # MUST succeed before any network operation
            try:
                with httpx.Client(timeout=120, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                    with client.stream('POST', ENDPOINT, content=wire, headers={'Authorization': 'Bearer ' + grant['api_key'], 'Content-Type': 'application/json'}) as response:
                        if response.status_code != 200:
                            raise TextError('MODEL_HTTP_ERROR', f'模型服务返回 HTTP {response.status_code}；本次已计入请求预算，可稍后重试当前片段。', 502)
                        data = bytearray()
                        for block in response.iter_bytes():
                            data.extend(block)
                            if len(data) > 2_000_000: raise TextError('MODEL_RESPONSE_TOO_LARGE', '模型响应过大，已停止。', 502)
                # Preserve provider payload privately before JSON validation, never in ordinary logs.
                # Enables diagnosis of malformed model JSON without paying to reproduce it.
                atomic_json(self.path.parent/'meeting-model-responses'/fingerprint(grant['batch_id'])/f"{row['number']:04d}.json",
                            {'stage':stage,'raw_response':data.decode('utf-8',errors='replace').replace(grant['api_key'],'[REDACTED_API_KEY]')})
                body = strict_json(data)
                usage = body.get('usage', {})
                i, o = usage.get('prompt_tokens'), usage.get('completion_tokens')
                if type(i) is not int or type(o) is not int or min(i, o) <= 0:
                    ledger['halted'] = True
                    raise TextError('MODEL_USAGE_MISSING', '模型未返回有效用量，预算已暂停，不能自动继续。', 502)
                row['usage'] = {'prompt_tokens': i, 'completion_tokens': o}
                choice = body['choices'][0]
                # A response cut at max_tokens may report a few tokens over the cap; that is
                # truncation (charged in full), not an unexplained meter overrun.
                if choice.get('finish_reason') == 'length' and i <= reserve and o <= limit + 64:
                    row['charged_input'], row['charged_output'] = i, max(o, limit)
                    raise TextError('MODEL_TRUNCATED', '模型输出未完整结束，未把截断内容保存为成果；请重试当前片段。', 502)
                if i > reserve or o > limit:
                    ledger['halted'] = True
                    row['charged_input'], row['charged_output'] = max(i, reserve), max(o, limit)
                    raise TextError('MODEL_USAGE_BOUND', '用量异常，已暂停 AI 处理。', 502)
                if choice['finish_reason'] != 'stop':
                    raise TextError('MODEL_TRUNCATED', '模型输出未完整结束，未把截断内容保存为成果；请重试当前片段。', 502)
                content = choice['message']['content']
                if not isinstance(content, str) or not content.strip(): raise ValueError()
                result = strict_json(content)
                row['charged_input'], row['charged_output'] = i, o
                row['status'] = 'ok'
                return result
            except TextError as exc:
                row['status'] = exc.code
                raise
            except httpx.TimeoutException:
                row['status'] = 'MODEL_TIMEOUT'
                raise TextError('MODEL_TIMEOUT', '模型响应超时，已保留成功片段；本次请求预留不退回，可重试。', 502) from None
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
                row['status'] = 'MODEL_INVALID_RESPONSE'
                raise TextError('MODEL_INVALID_RESPONSE', '模型连接中断或返回格式无效，已保留成功片段，可重试。', 502) from None
            finally:
                row['finished_at'] = time.time()
                ledger['conservative_cny'] = sum(r['charged_input'] * INPUT_MICROYUAN + r['charged_output'] * OUTPUT_MICROYUAN for r in ledger['requests']) / 1_000_000
                atomic_json(ledger_path, ledger)

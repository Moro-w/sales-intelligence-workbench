"""Offline text-only contracts. No model calls and no ASR dependencies."""
import hashlib
import json
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from app import text_storage as store
from app.text_main import create_app

TOKEN = 'a' * 64
MID = '1' * 32
CONTENT = '\ufeff# 测试原文\r\n甲：不保证周五完成😀。\r\n\r\n乙：确认后再开始。\n<script>window.testInjected=true</script>\n'.encode('utf-8')
HEADERS = {'x-tingji-service-token': TOKEN, 'x-tingji-user-id': 'user-a'}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'meetings', TOKEN), headers=HEADERS) as c:
        yield c


def upload(client, content=CONTENT, filename='原文.md', mid=MID, title='同名会议'):
    return client.post('/api/imports', files={'file': (filename, content, 'text/markdown')},
                       data={'title': title, 'import_id': mid})


def test_no_model_or_asr_loaded_and_no_implicit_start(client):
    assert client.get('/api/status').json()['model_enabled'] is False
    assert client.get('/api/meetings').json() == []
    assert not {'torch', 'torchaudio', 'funasr', 'app.asr', 'app.llm', 'app.tasks'} & sys.modules.keys()
    for path in ['/api/settings', '/api/tasks', '/docs', '/openapi.json', '/ui/static/access.js']:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize('headers', [{}, {'x-tingji-service-token': 'bad'}, {'x-tingji-service-token': TOKEN}])
def test_direct_access_requires_both_trusted_service_and_owner(tmp_path, headers):
    with TestClient(create_app(tmp_path, TOKEN)) as c:
        assert c.get('/api/status', headers=headers).status_code == 401


def test_save_reopen_raw_hash_index_and_readonly(client, tmp_path):
    assert upload(client).status_code == 201
    path = store.meeting_dir('user-a', MID)
    assert set(p.name for p in path.iterdir()) == {'original.md', 'source-index.json', 'meta.json'}
    assert stat.S_IMODE((path / 'original.md').stat().st_mode) == 0o444
    assert (path / 'original.md').read_bytes() == CONTENT
    # New app instance reads from disk, not an in-memory task cache.
    with TestClient(create_app(tmp_path / 'meetings', TOKEN), headers=HEADERS) as reopened:
        data = reopened.get(f'/api/meetings/{MID}').json()
        assert data['meta']['status'] == 'draft'
        assert data['processed'] is None and data['summary'] is None
        assert data['meta']['source_sha256'] == hashlib.sha256(CONTENT).hexdigest()
        utf16 = data['source_text'].encode('utf-16-le')
        indexed = data['source_index']['segments']
        rebuilt = ''.join(utf16[s['start_utf16'] * 2:s['end_utf16'] * 2].decode('utf-16-le') for s in indexed)
        assert rebuilt == CONTENT.decode('utf-8-sig')
        assert len(indexed) == data['meta']['line_count'] == 5
        assert indexed[1]['segment_id'] == f'{MID}:L2'
        assert reopened.get(f'/api/meetings/{MID}/source').content == CONTENT
        assert reopened.put(f'/api/meetings/{MID}/source', content=b'changed').status_code == 405


def test_owner_cannot_read_another_owner(client):
    assert upload(client).status_code == 201
    other = {'x-tingji-user-id': 'user-b'}
    assert client.get('/api/meetings', headers=other).json() == []
    for path in [f'/api/meetings/{MID}', f'/api/meetings/{MID}/source', f'/ui/m/{MID}']:
        assert client.get(path, headers=other).status_code == 404
    assert client.get('/api/meetings').json()[0]['id'] == MID


def test_idempotency_conflict_and_same_title_isolation(client):
    assert upload(client).status_code == 201
    repeated = upload(client)
    assert repeated.status_code == 200 and repeated.json()['created'] is False
    assert upload(client, content=b'other').status_code == 409
    assert upload(client, title='changed').status_code == 409
    assert upload(client, mid='2' * 32).status_code == 201
    assert len(client.get('/api/meetings').json()) == 2
    assert client.get(f'/api/meetings/{MID}/source').content == CONTENT


@pytest.mark.parametrize('content, filename, status', [
    (b'', 'empty.md', 400), (b' \r\n ', 'empty.md', 400), (b'\xff\xfe', 'bad.md', 400),
    (b'abc\x00', 'binary.md', 400), (b'text', 'audio.mp3', 415), (b'text', '../secret.md', 415),
    (b'x' * (store.MAX_SOURCE_BYTES + 1), 'large.md', 413), (b'a\n' * 20_001, 'lines.md', 413),
])
def test_invalid_input_never_creates_meeting(client, content, filename, status):
    response = upload(client, content, filename)
    assert response.status_code == status
    assert client.get('/api/meetings').json() == []


@pytest.mark.parametrize('mid', ['../escape', 'x' * 32, '123', 'A' * 32])
def test_bad_id_does_not_escape_root(client, mid):
    assert upload(client, mid=mid).status_code == 404
    assert client.get('/api/meetings').json() == []


def test_concurrent_same_id_has_one_creation(client):
    def create(_):
        return store.import_markdown('user-a', MID, '会议.md', CONTENT, '并发测试')[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(create, range(8))) == [False] * 7 + [True]
    assert len(client.get('/api/meetings').json()) == 1


def test_failure_before_publish_leaves_no_partial_meeting_and_can_retry(client):
    with patch.object(store.storage, '_write_meta', side_effect=OSError('simulated disk failure')):
        response = upload(client)
    assert response.status_code == 500
    assert response.json()['code'] == 'STORAGE_FAILED'
    assert client.get('/api/meetings').json() == []
    assert not list(store.meeting_dir('user-a', MID).parent.glob('.import-*'))
    assert upload(client).status_code == 201


@pytest.mark.parametrize('corruption', ['source', 'index', 'meta', 'missing'])
def test_corrupt_files_are_not_reported_as_success(client, corruption):
    assert upload(client).status_code == 201
    directory = store.meeting_dir('user-a', MID)
    if corruption == 'source':
        original = directory / 'original.md'
        original.chmod(0o600)
        original.write_bytes(b'tampered')
    elif corruption == 'index':
        (directory / 'source-index.json').write_text('{}')
    elif corruption == 'meta':
        (directory / 'meta.json').write_text('["invalid"]')
    else:
        (directory / 'source-index.json').unlink()
    assert client.get(f'/api/meetings/{MID}').status_code == 409
    # New core-processing contract: a corrupt row is explicit, but does not block other meetings.
    response = client.get('/api/meetings')
    assert response.status_code == 200
    assert response.json()[0]['status'] == 'failed'
    assert response.json()[0]['error']['code'] in ('SOURCE_CHANGED', 'INDEX_CHANGED', 'STORAGE_CORRUPT')


def test_pages_reuse_native_html_with_only_same_origin_asset_paths(client):
    response = client.get('/ui/')
    assert response.status_code == 200
    assert '/api/meeting-assistant/ui/static/app.js' in response.text
    assert '/api/meeting-assistant/ui/static/text-mode.js' in response.text
    assert '/access.js' not in response.text
    assert 'src="/static/' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert upload(client).status_code == 201
    response = client.get(f'/ui/m/{MID}')
    assert '/api/meeting-assistant/ui/static/meeting.js' in response.text
    assert 'window.testInjected=true' not in response.text  # user source only arrives as JSON

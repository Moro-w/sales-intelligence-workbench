// Offline native Tingji helper checks, not browser/business acceptance.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../Tingji/static/common.js', import.meta.url), 'utf8');
function context() {
  const calls = [];
  const ctx = vm.createContext({ window: { TINGJI_TEXT_ONLY: true }, document: { cookie: '' }, Headers,
    fetch: (...args) => { calls.push(args); return Promise.resolve({ ok: true }); } });
  vm.runInContext(source, ctx);
  return { ctx, calls };
}
test('native URLs stay inside the fixed same-origin gateway', () => {
  const { ctx } = context();
  assert.equal(ctx.tingjiUrl('/api/meetings'), '/api/meeting-assistant/meetings');
  assert.equal(ctx.meetingUrl('a'.repeat(32)), '/api/meeting-assistant/ui/m/' + 'a'.repeat(32));
});
test('reads do not synthesize a CSRF header or call a model', async () => {
  const { ctx, calls } = context();
  await ctx.apiFetch('/api/meetings');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/meeting-assistant/meetings');
  assert.equal(calls[0][1].headers.has('x-csrf-token'), false);
  assert.equal(calls[0][1].credentials, 'same-origin');
});
test('mutations use the exact current cookie and re-read it after rotation', async () => {
  const { ctx, calls } = context();
  ctx.document.cookie = 'not-aionui-csrf-token=bad;aionui-csrf-token=' + 'a'.repeat(64);
  await ctx.apiFetch('/api/imports', { method: 'POST' });
  ctx.document.cookie = 'aionui-csrf-token=' + 'b'.repeat(64);
  await ctx.apiFetch('/api/imports', { method: 'POST' });
  assert.equal(calls[0][1].headers.get('x-csrf-token'), 'a'.repeat(64));
  assert.equal(calls[1][1].headers.get('x-csrf-token'), 'b'.repeat(64));
});
test('missing, malformed and prefixed cookies fail closed before network', async () => {
  for (const cookie of ['', 'aionui-csrf-token=bad', 'evil-aionui-csrf-token=' + 'a'.repeat(64), 'aionui-csrf-token=' + 'a'.repeat(64) + '=extra']) {
    const { ctx, calls } = context();
    ctx.document.cookie = cookie;
    await assert.rejects(() => ctx.apiFetch('/api/imports', { method: 'POST' }), /登录状态/);
    assert.equal(calls.length, 0);
  }
});
test('legacy mode retains its native request contracts', async () => {
  const { ctx, calls } = context();
  ctx.window.TINGJI_TEXT_ONLY = false;
  await ctx.apiFetch('/api/upload', { method: 'POST' });
  assert.equal(calls[0][0], '/api/upload');
  assert.equal(ctx.meetingUrl('legacy-id'), '/m/legacy-id');
});

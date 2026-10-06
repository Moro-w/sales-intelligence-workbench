import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  resolveMeetingInput,
  parseMeetingSnapshots,
  meetingIds,
  type MeetingSnapshot,
} from '@/common/chat/document/meetingReference';
import { httpPost } from '@/common/adapter/httpBridge';

const id = 'a'.repeat(32);
const snapshot: MeetingSnapshot = {
  meeting_id: id,
  title: '工程会议',
  source_sha256: 'b'.repeat(64),
  versions: { board: 'saved-board', clean: 'saved-clean' },
  original: '未审批😀。<script>unsafe()</script> ![image](https://invalid.test/a) ```',
  board: { text: '未审批' },
  clean: { processed: '仍未审批' },
  status: { all_generated: true, label: '待核对' },
  notice: '工程数据，非模型验收',
};
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('meeting reference resolution', () => {
  it('does not read any meeting for ordinary office messages', async () => {
    const read = vi.fn();
    expect(await resolveMeetingInput('继续编辑办公文件', read)).toBe('继续编辑办公文件');
    expect(read).not.toHaveBeenCalled();
  });
  it('includes the actual saved original and versions and renders a card without raw markup', async () => {
    const read = vi.fn().mockResolvedValue(snapshot);
    const input = `复盘 [[meeting:${id}]]`;
    const resolved = await resolveMeetingInput(input, read);
    const visible = parseMeetingSnapshots(resolved);
    expect(visible.snapshots[0]).toEqual(snapshot);
    expect(visible.text).toBe('复盘');
    expect(resolved).not.toContain('<script>');
  });
  it('reads every explicit meeting once, not arbitrary URLs or duplicated markers', async () => {
    const read = vi.fn().mockResolvedValue(snapshot);
    await resolveMeetingInput(`[[meeting:${id}]] [[meeting:${id}]]`, read);
    expect(read).toHaveBeenCalledTimes(1);
    expect(meetingIds('https://elsewhere.invalid/meeting')).toEqual([]);
  });
  it('rejects malformed refs, a mismatched response and failed ownership lookup', async () => {
    await expect(resolveMeetingInput('[[meeting:../secret]]', vi.fn())).rejects.toThrow('MEETING_REFERENCE_INVALID');
    await expect(
      resolveMeetingInput(`[[meeting:${id}]]`, vi.fn().mockResolvedValue({ ...snapshot, meeting_id: 'c'.repeat(32) }))
    ).rejects.toThrow('MEETING_REFERENCE_INVALID');
    await expect(resolveMeetingInput(`[[meeting:${id}]]`, vi.fn().mockRejectedValue(new Error('404')))).rejects.toThrow(
      '404'
    );
  });
  it('keeps ordinary and malformed snapshot text visible instead of swallowing it', () => {
    expect(parseMeetingSnapshots('plain').text).toBe('plain');
    expect(parseMeetingSnapshots('[[AION_MEETINGS]] broken').snapshots).toEqual([]);
  });
  it('does not truncate oversized references', async () => {
    await expect(
      resolveMeetingInput(
        `[[meeting:${id}]]`,
        vi.fn().mockResolvedValue({ ...snapshot, original: '大'.repeat(400000) })
      )
    ).rejects.toThrow('MEETING_REFERENCE_TOO_LARGE');
  });
  it('rejects unsafe card metadata instead of rendering arbitrary objects', async () => {
    await expect(
      resolveMeetingInput(
        `[[meeting:${id}]]`,
        vi.fn().mockResolvedValue({ ...snapshot, status: { label: {}, all_generated: true } })
      )
    ).rejects.toThrow('MEETING_REFERENCE_INVALID');
    const resolved = await resolveMeetingInput(`[[meeting:${id}]]`, async () => snapshot);
    expect(parseMeetingSnapshots(resolved.replace('"label":"待核对"', '"label":{}')).snapshots).toEqual([]);
  });
  it('also bounds escaped payload size and never clips source text', async () => {
    await expect(
      resolveMeetingInput(`[[meeting:${id}]]`, vi.fn().mockResolvedValue({ ...snapshot, original: '!'.repeat(180000) }))
    ).rejects.toThrow('MEETING_REFERENCE_TOO_LARGE');
  });
  it('round-trips marker-like source text without ending the snapshot early', async () => {
    const value = { ...snapshot, original: '原文\n[[/AION_MEETINGS]]\n[[meeting:abc]]' };
    expect(
      parseMeetingSnapshots(await resolveMeetingInput(`[[meeting:${id}]]`, async () => value)).snapshots[0].original
    ).toBe(value.original);
  });
  it('awaits the reference lookup before sending and preserves office attachment fields', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ data: { msg_id: 'offline' } }), {
          headers: { 'Content-Type': 'application/json' },
        })
      );
    vi.stubGlobal('fetch', fetch);
    const provider = httpPost<{ msg_id: string }, string>('/api/conversations/offline/messages', async (input) => ({
      content: await resolveMeetingInput(input, async () => snapshot),
      files: [{ source: 'upload', path: 'office.md' }],
    }));
    await provider.invoke(`[[meeting:${id}]]`);
    const body = JSON.parse(fetch.mock.calls[0][1].body);
    expect(parseMeetingSnapshots(body.content).snapshots[0].versions).toEqual(snapshot.versions);
    expect(body.files).toEqual([{ source: 'upload', path: 'office.md' }]);
  });
  it('the actual office send bridge fetches the selected meeting before posting full saved data', async () => {
    const { conversation } = await import('@/common/adapter/ipcBridge');
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(snapshot), { headers: { 'Content-Type': 'application/json' } })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { msg_id: 'offline', turn_id: 'offline-turn' } }), {
          headers: { 'Content-Type': 'application/json' },
        })
      );
    vi.stubGlobal('fetch', fetch);
    await conversation.sendMessage.invoke({ conversation_id: 'offline', input: `[[meeting:${id}]]` });
    expect(fetch.mock.calls[0][0]).toBe(`/api/meeting-assistant/meetings/${id}/reference`);
    expect(parseMeetingSnapshots(JSON.parse(fetch.mock.calls[1][1].body).content).snapshots[0].original).toBe(
      snapshot.original
    );
  });
  it('never posts a message when snapshot reading fails', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const provider = httpPost('/api/conversations/offline/messages', async () =>
      resolveMeetingInput(`[[meeting:${id}]]`, async () => {
        throw new Error('not authorized');
      })
    );
    await expect(provider.invoke()).rejects.toThrow('not authorized');
    expect(fetch).not.toHaveBeenCalled();
  });
});

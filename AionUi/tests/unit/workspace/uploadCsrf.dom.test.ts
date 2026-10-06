import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadFileViaHttp } from '@/renderer/services/FileService';
import { transcribeAudioBlob } from '@/renderer/services/SpeechToTextService';

vi.mock('@/renderer/hooks/file/useUploadState', () => ({ trackUpload: vi.fn() }));
vi.mock('@/renderer/services/i18n/format', () => ({ formatByteSize: vi.fn() }));

class TestXhr extends EventTarget {
  static instances: TestXhr[] = [];
  upload = new EventTarget();
  open = vi.fn();
  send = vi.fn();
  setRequestHeader = vi.fn();
  abort = vi.fn(() => this.dispatchEvent(new Event('abort')));
  status = 200;
  statusText = 'OK';
  responseText = JSON.stringify({ success: true, data: '/test/upload.md' });

  constructor() {
    super();
    TestXhr.instances.push(this);
  }
}

beforeEach(() => {
  delete window.__backendPort;
  document.cookie = `aionui-csrf-token=${'a'.repeat(64)}; Path=/`;
  TestXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', TestXhr);
});

afterEach(() => {
  document.cookie = 'aionui-csrf-token=; Max-Age=0; Path=/';
  vi.unstubAllGlobals();
});

describe('authenticated multipart uploads', () => {
  it.each(['file', 'speech'])('attaches the double-submit header to %s uploads', async (kind) => {
    const pending =
      kind === 'file'
        ? uploadFileViaHttp(new File(['fixture'], 'fixture.md'))
        : transcribeAudioBlob(new Blob(['fixture'], { type: 'audio/webm' }));
    const xhr = TestXhr.instances[0];
    if (kind === 'speech') xhr.responseText = JSON.stringify({ success: true, data: { text: 'fixture' } });
    xhr.dispatchEvent(new Event('load'));
    await pending;
    expect(xhr.setRequestHeader).toHaveBeenCalledWith('x-csrf-token', 'a'.repeat(64));
    expect(xhr.send).toHaveBeenCalledWith(expect.any(FormData));
  });

  it('reports a server rejection instead of resolving an upload path', async () => {
    const pending = uploadFileViaHttp(new File(['fixture'], 'fixture.md'));
    const xhr = TestXhr.instances[0];
    xhr.status = 403;
    xhr.statusText = 'Forbidden';
    xhr.dispatchEvent(new Event('load'));
    await expect(pending).rejects.toThrow('Upload failed: 403 Forbidden');
  });

  it('does not send an upload that was cancelled before it started', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      uploadFileViaHttp(new File(['fixture'], 'fixture.md'), undefined, undefined, undefined, {
        signal: controller.signal,
      })
    ).rejects.toThrow('Upload aborted');
    expect(TestXhr.instances[0].send).not.toHaveBeenCalled();
  });
});

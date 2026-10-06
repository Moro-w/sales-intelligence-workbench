import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getCoreCsrfHeaders, httpRequest, resolveCoreCsrfToken } from '@/common/adapter/httpBridge';

const TOKEN = 'b'.repeat(64);

beforeEach(() => {
  delete window.__backendPort;
  document.cookie = `aionui-csrf-token=${TOKEN}; Path=/`;
});

afterEach(() => {
  delete window.__backendPort;
  document.cookie = 'aionui-csrf-token=; Max-Age=0; Path=/';
  vi.unstubAllGlobals();
});

describe('Core CSRF double-submit contract', () => {
  it('reads the exact Core cookie without caching it', () => {
    expect(resolveCoreCsrfToken()).toBe(TOKEN);
    document.cookie = `aionui-csrf-token=${'c'.repeat(64)}; Path=/`;
    expect(resolveCoreCsrfToken()).toBe('c'.repeat(64));
  });

  it('does not send browser credentials to a desktop local-mode backend', () => {
    window.__backendPort = 13400;
    expect(getCoreCsrfHeaders()).toEqual({});
  });

  it('rejects malformed tokens instead of putting arbitrary cookie text into a header', () => {
    document.cookie = 'aionui-csrf-token=invalid; Path=/';
    expect(getCoreCsrfHeaders()).toEqual({});
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('attaches the CSRF token on %s requests', async (method) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('{}', { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    await httpRequest(method, '/api/test', { title: 'test' });
    expect(fetchMock.mock.calls[0][1].headers['x-csrf-token']).toBe(TOKEN);
  });

  it('does not attach the CSRF token to safe reads', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetchMock);
    await httpRequest('GET', '/api/test');
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty('x-csrf-token');
  });

  it('does not turn a CSRF rejection into a silent success or retry loop', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ code: 'CSRF_INVALID' }), { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(httpRequest('POST', '/api/test', {})).rejects.toMatchObject({ status: 403, code: 'CSRF_INVALID' });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

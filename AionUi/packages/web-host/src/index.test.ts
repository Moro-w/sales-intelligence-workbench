import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./backend-launcher.js', () => ({ startBackend: vi.fn() }));
vi.mock('./static-server.js', () => ({ startStaticServer: vi.fn() }));

import { startWebHost } from './index.js';
import { startBackend } from './backend-launcher.js';
import { startStaticServer } from './static-server.js';
import type { WebHostOptions } from './types.js';

const opts: WebHostOptions = {
  app: { version: '1.0.0', isPackaged: false, resourcesPath: '/app', userDataPath: '/data' },
  staticDir: '/static',
  dataDir: '/data',
  backend: { kind: 'ownBackend', resolveBackend: () => '/bin/backend' },
};

const stopBackend = vi.fn().mockResolvedValue(undefined);
const stopStatic = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(startBackend).mockResolvedValue({ port: 55555, stop: stopBackend });
  vi.mocked(startStaticServer).mockResolvedValue({
    port: 25849,
    url: 'http://127.0.0.1:25849',
    localUrl: 'http://127.0.0.1:25849',
    stop: stopStatic,
  });
});

describe('WebHost authenticated backend ownership', () => {
  it('never disables backend authentication for its browser-facing backend', async () => {
    const handle = await startWebHost(opts);
    expect(startBackend).toHaveBeenCalledWith(expect.objectContaining({ local: false }));
    await handle.stop();
    expect(stopBackend).toHaveBeenCalledOnce();
  });

  it('stops its backend if the static listener fails', async () => {
    vi.mocked(startStaticServer).mockRejectedValueOnce(new Error('port occupied'));
    await expect(startWebHost(opts)).rejects.toThrow('port occupied');
    expect(stopBackend).toHaveBeenCalledOnce();
  });

  it('does not restart or stop a caller-owned desktop backend', async () => {
    const handle = await startWebHost({ ...opts, backend: { kind: 'useExistingBackend', port: 55554 } });
    await handle.stop();
    expect(startBackend).not.toHaveBeenCalled();
    expect(stopBackend).not.toHaveBeenCalled();
  });
});

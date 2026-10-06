import React from 'react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import config from '@/common/config/i18n-config.json';
import zh from '@/renderer/services/i18n/locales/zh-CN/login.json';

const { login, navigate } = vi.hoisted(() => ({ login: vi.fn(), navigate: vi.fn() }));
vi.mock('@/renderer/hooks/context/AuthContext', () => ({
  useAuth: () => ({ status: 'unauthenticated', login }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));
vi.mock('@/renderer/services/i18n', () => ({ changeLanguage: vi.fn().mockResolvedValue(undefined) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh-CN' },
    t: (key: string) => {
      const value = key
        .split('.')
        .slice(1)
        .reduce<unknown>(
          (obj, part) => (obj && typeof obj === 'object' ? (obj as Record<string, unknown>)[part] : undefined),
          zh
        );
      return typeof value === 'string' ? value : key;
    },
  }),
}));

import LoginPage from '@/renderer/pages/login';

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe('workbench login branding preserves authentication behavior', () => {
  it('shows the sales brand name and S mark without the upstream wordmark', () => {
    const { container } = render(<LoginPage />);
    expect(screen.getByRole('heading', { name: '销售智能工作台' })).toBeInTheDocument();
    expect(container.querySelector('.login-page__logo svg text')?.textContent).toBe('S');
    expect(container.textContent).not.toMatch(/aionui/i);
  });

  it('keeps empty credentials blocked after the field labels change', () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请输入登录账号和密码');
    expect(login).not.toHaveBeenCalled();
  });

  it('keeps the username/password contract and displays a rejected login', async () => {
    login.mockResolvedValueOnce({ success: false, code: 'invalidCredentials' });
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText('登录账号'), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText('登录密码'), { target: { value: 'test-only-password' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('登录账号或密码错误'));
    expect(login).toHaveBeenCalledWith({ username: 'admin', password: 'test-only-password', remember: false });
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('brand metadata', () => {
  it.each(config.supportedLanguages)('does not revert to the old brand in %s', (locale) => {
    const file = path.resolve('packages/desktop/src/renderer/services/i18n/locales', locale, 'login.json');
    const raw = readFileSync(file, 'utf8');
    const messages = JSON.parse(raw) as { brand: string; pageTitle: string };
    expect(messages.brand).toBe('销售智能工作台');
    expect(messages.pageTitle).toContain(messages.brand);
    expect(raw).not.toMatch(/aionui/i);
  });

  it('uses the workbench name and new icon URLs before React loads', () => {
    const html = readFileSync('packages/desktop/src/renderer/index.html', 'utf8');
    expect(html).toContain('<title>销售智能工作台</title>');
    expect(html).toContain('href="./pwa/workbench.svg"');
    expect(html).not.toMatch(/content="AionUi"|pwa\/icon-/);
  });

  it('points the installable app at existing new icons, not the old brand assets', () => {
    const manifest = JSON.parse(readFileSync('public/manifest.webmanifest', 'utf8')) as {
      name: string;
      icons: { src: string }[];
    };
    expect(manifest.name).toBe('销售智能工作台');
    for (const icon of manifest.icons) {
      expect(icon.src).toContain('workbench-');
      expect(readFileSync(path.join('public', icon.src)).byteLength).toBeGreaterThan(0);
    }
  });
});

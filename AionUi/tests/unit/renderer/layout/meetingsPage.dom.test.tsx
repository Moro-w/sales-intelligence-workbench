import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { meetingFrameSrc, meetingRouteFromMessage } from '@/renderer/pages/meetings/frameContract';
const { navigate, route } = vi.hoisted(() => ({
  navigate: vi.fn(),
  route: { id: undefined as string | undefined, pathname: '/meetings' },
}));
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ id: route.id }),
  useLocation: () => ({ pathname: route.pathname }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import MeetingsPage from '@/renderer/pages/meetings';
import SiderMeetingEntry from '@/renderer/components/layout/Sider/SiderNav/SiderMeetingEntry';

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  route.id = undefined;
  route.pathname = '/meetings';
});
afterEach(() => {
  vi.useRealTimers();
});

it('restricts frame paths and navigation messages to meeting ids', () => {
  expect(meetingFrameSrc()).toBe('/api/meeting-assistant/ui/');
  expect(meetingFrameSrc('a'.repeat(32))).toBe('/api/meeting-assistant/ui/m/' + 'a'.repeat(32));
  expect(meetingFrameSrc('../providers')).toBeUndefined();
  expect(meetingRouteFromMessage({ type: 'tingji.ready', meetingId: null })).toBe('/meetings');
  expect(meetingRouteFromMessage({ type: 'tingji.ready', meetingId: 'b'.repeat(32) })).toBe(
    '/meetings/' + 'b'.repeat(32)
  );
  for (const value of [
    null,
    {},
    { type: 'tingji.ready' },
    { type: 'tingji.ready', meetingId: '../login' },
    { type: 'evil', meetingId: null },
  ]) {
    expect(meetingRouteFromMessage(value)).toBeUndefined();
  }
});

it('opens the trusted embedded page and ignores forged navigation messages', () => {
  const { container } = render(<MeetingsPage />);
  const iframe = container.querySelector('iframe')!;
  expect(iframe.src).toContain('/api/meeting-assistant/ui/');
  expect(screen.getByRole('status')).toBeInTheDocument();
  const data = { type: 'tingji.ready', meetingId: 'c'.repeat(32) };
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', { data, origin: 'https://evil.test', source: iframe.contentWindow })
    )
  );
  act(() =>
    window.dispatchEvent(new MessageEvent('message', { data, origin: window.location.origin, source: window }))
  );
  expect(navigate).not.toHaveBeenCalled();
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', { data, origin: window.location.origin, source: iframe.contentWindow })
    )
  );
  expect(navigate).toHaveBeenCalledWith('/meetings/' + 'c'.repeat(32), { replace: true });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('shows an actionable timeout and remounts the frame on retry', () => {
  const { container } = render(<MeetingsPage />);
  const original = container.querySelector('iframe');
  act(() => vi.advanceTimersByTime(10000));
  expect(screen.getByRole('alert')).toHaveTextContent('common.meetingsUnavailable');
  fireEvent.click(screen.getByRole('button', { name: 'common.meetingsRetry' }));
  expect(container.querySelector('iframe')).not.toBe(original);
  expect(screen.getByRole('status')).toBeInTheDocument();
});

it('does not load a malformed deep link', () => {
  route.id = '../outside';
  const { container } = render(<MeetingsPage />);
  expect(container.querySelector('iframe')).toBeNull();
  expect(screen.getByRole('alert')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'common.meetingsBack' }));
  expect(navigate).toHaveBeenCalledWith('/meetings');
});

it('keeps the meeting entry accessible in collapsed and expanded sidebars', () => {
  const onClick = vi.fn();
  const { rerender } = render(<SiderMeetingEntry collapsed={false} isActive onClick={onClick} />);
  const button = screen.getByRole('button', { name: 'common.meetingsTitle' });
  expect(button).toHaveAttribute('aria-current', 'page');
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledOnce();
  rerender(<SiderMeetingEntry collapsed isActive={false} onClick={onClick} />);
  expect(screen.getByRole('button', { name: 'common.meetingsTitle' })).not.toHaveAttribute('aria-current');
});

import React from 'react';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import GuidInputCard from '@/renderer/pages/guid/components/GuidInputCard';

vi.mock('@/renderer/components/chat/SendBox', async () => ({
  MeetingReferenceCards: (await import('@/renderer/components/chat/SendBox/MeetingReferenceCards')).default,
}));
vi.mock('@/renderer/components/media/FilePreview', () => ({ default: () => null }));
vi.mock('@/renderer/components/media/UploadProgressBar', () => ({ default: () => null }));
vi.mock('@/renderer/pages/guid/components/GuidWorkspaceFootnote', () => ({ default: () => null }));
vi.mock('@/renderer/hooks/context/LayoutContext', () => ({ useLayoutContext: () => ({ isMobile: false }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
const id = 'a'.repeat(32);
function mount(input: string) {
  const onInputChange = vi.fn();
  render(
    <GuidInputCard
      input={input}
      onInputChange={onInputChange}
      onKeyDown={vi.fn()}
      onPaste={vi.fn()}
      onFocus={vi.fn()}
      onBlur={vi.fn()}
      placeholder='office'
      isInputActive={false}
      isFileDragging={false}
      activeBorderColor=''
      inactiveBorderColor=''
      activeShadow=''
      dragHandlers={{}}
      files={[]}
      onRemoveFile={vi.fn()}
      actionRow={null}
      workspaceDir=''
      onSelectWorkspace={vi.fn()}
      onClearWorkspace={vi.fn()}
    />
  );
  return onInputChange;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it('ordinary new-chat input does not fetch meetings', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  mount('正常办公文字');
  expect(screen.queryByTestId('meeting-reference-cards')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
it('new-chat input previews actual reference data and allows removal without sending', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ meeting_id: id, title: '工程资料，不是AI结果', status: { label: '尚未处理' } }), {
        headers: { 'content-type': 'application/json' },
      })
    );
  vi.stubGlobal('fetch', fetch);
  const change = mount(`请查看 [[meeting:${id}]]`);
  await screen.findByText('工程资料，不是AI结果');
  expect(fetch.mock.calls[0][1].method).toBe('GET');
  fireEvent.click(screen.getByText('common.remove'));
  expect(change).toHaveBeenCalledWith('请查看 ');
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('failed preview has an explicit error and does not post a message', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response('{}', { status: 404, headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', fetch);
  mount(`[[meeting:${id}]]`);
  await waitFor(() => expect(screen.getByText('common.meetingReferenceLoadError')).toBeTruthy());
  expect(fetch.mock.calls.every((call) => call[1].method === 'GET')).toBe(true);
});

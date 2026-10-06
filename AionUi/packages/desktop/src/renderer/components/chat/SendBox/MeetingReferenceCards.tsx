import React, { useEffect, useMemo, useState } from 'react';
import { Button } from '@arco-design/web-react';
import { useTranslation } from 'react-i18next';
import { httpRequest } from '@/common/adapter/httpBridge';
import { meetingIds, meetingMarker, type MeetingSnapshot } from '@/common/chat/document/meetingReference';

type Props = {
  input?: string;
  onChange?: (value: string) => void;
  snapshots?: MeetingSnapshot[];
};

/** Preview explicit references only. Sending re-reads saved versions; preview never sends a model request. */
export default function MeetingReferenceCards({ input = '', onChange, snapshots }: Props) {
  const { t } = useTranslation();
  const idsKey = useMemo(() => meetingIds(input).join(','), [input]);
  const [records, setRecords] = useState<Record<string, MeetingSnapshot | null>>({});
  const [failed, setFailed] = useState<string[]>([]);
  useEffect(() => {
    if (snapshots) return;
    let alive = true;
    setRecords({});
    setFailed([]);
    for (const id of idsKey.split(',').filter(Boolean).slice(0, 3)) {
      void httpRequest<MeetingSnapshot>('GET', `/api/meeting-assistant/meetings/${id}/reference`).then(
        (value) => {
          if (alive) setRecords((prior) => ({ ...prior, [id]: value }));
        },
        () => {
          if (alive) setFailed((prior) => [...prior, id]);
        }
      );
    }
    return () => {
      alive = false;
    };
  }, [idsKey, snapshots]);
  const ids = snapshots ? snapshots.map((s) => s.meeting_id) : idsKey.split(',').filter(Boolean);
  if (ids.length === 0) return null;
  return (
    <div className='flex flex-col gap-8px mb-8px' data-testid='meeting-reference-cards'>
      {!snapshots && <div className='text-12px text-t-secondary'>{t('common.meetingReferencePreview')}</div>}
      {ids.map((id) => {
        const record = snapshots?.find((s) => s.meeting_id === id) ?? records[id];
        return (
          <section key={id} className='p-12px rd-8px b-1 b-solid b-border-2 bg-fill-1 min-w-0'>
            <div className='text-14px text-t-primary break-words'>{record?.title ?? t('common.meetingsTitle')}</div>
            <div className='text-12px text-t-secondary break-all'>
              {failed.includes(id)
                ? t('common.meetingReferenceLoadError')
                : record
                  ? record.status?.label
                  : t('common.loading')}
            </div>
            {snapshots && record && (
              <div className='text-12px text-t-secondary break-all'>
                {t('common.meetingReferenceSnapshot', {
                  board: record.versions.board ?? '—',
                  clean: record.versions.clean ?? '—',
                })}
              </div>
            )}
            <div className='flex gap-8px mt-4px'>
              <Button type='text' size='mini' href={`#/meetings/${id}`}>
                {t('common.meetingReferenceOpen')}
              </Button>
              {onChange && (
                <Button type='text' size='mini' onClick={() => onChange(input.split(meetingMarker(id)).join(''))}>
                  {t('common.remove')}
                </Button>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

const PREFIX = '/api/meeting-assistant/ui/';
const MEETING_ID = /^[a-f0-9]{32}$/;

export function meetingFrameSrc(id?: string): string | undefined {
  if (id === undefined) return PREFIX;
  return MEETING_ID.test(id) ? `${PREFIX}m/${id}` : undefined;
}

export function meetingRouteFromMessage(data: unknown): string | undefined {
  if (!data || typeof data !== 'object' || !('type' in data) || data.type !== 'tingji.ready') return undefined;
  if (!('meetingId' in data)) return undefined;
  if (data.meetingId === null) return '/meetings';
  if (typeof data.meetingId === 'string' && MEETING_ID.test(data.meetingId)) return `/meetings/${data.meetingId}`;
  return undefined;
}

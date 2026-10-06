import React, { useEffect, useRef, useState } from 'react';
import { Button, Spin } from '@arco-design/web-react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { meetingFrameSrc, meetingRouteFromMessage } from './frameContract';
import styles from './styles.module.css';

export default function MeetingsPage() {
  const { id } = useParams<{ id: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const frame = useRef<HTMLIFrameElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const src = meetingFrameSrc(id);
  useEffect(() => {
    setState(src ? 'loading' : 'error');
    if (!src) return;
    const timer = window.setTimeout(() => setState('error'), 10000);
    const onMessage = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      const route = meetingRouteFromMessage(event.data);
      if (!route) return;
      window.clearTimeout(timer);
      setState('ready');
      if (route !== pathname) void navigate(route, { replace: true });
    };
    window.addEventListener('message', onMessage);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('message', onMessage);
    };
  }, [src, pathname, navigate, attempt]);
  return (
    <section className={styles.page} aria-label={t('common.meetingsTitle')}>
      {src && (
        <iframe
          key={attempt}
          ref={frame}
          src={src}
          title={t('common.meetingsTitle')}
          className={styles.frame}
          sandbox='allow-scripts allow-same-origin allow-downloads'
          referrerPolicy='same-origin'
          onError={() => setState('error')}
        />
      )}
      {state !== 'ready' && (
        <div className={styles.overlay} role={state === 'error' ? 'alert' : 'status'}>
          {state === 'loading' ? (
            <Spin tip={t('common.meetingsLoading')} />
          ) : (
            <>
              <p>{t('common.meetingsUnavailable')}</p>
              <div className='flex gap-12px'>
                <Button onClick={() => setAttempt((value) => value + 1)}>{t('common.meetingsRetry')}</Button>
                <Button onClick={() => void navigate('/meetings')}>{t('common.meetingsBack')}</Button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

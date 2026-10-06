import React from 'react';
import { Tooltip } from '@arco-design/web-react';
import { Notes } from '@icon-park/react';
import { useTranslation } from 'react-i18next';
import classNames from 'classnames';

type Props = { collapsed: boolean; isActive: boolean; onClick: () => void };

// Same row layout as the other sider entries (icon box + left-aligned label).
export default function SiderMeetingEntry({ collapsed, isActive, onClick }: Props) {
  const { t } = useTranslation();
  const label = t('common.meetingsTitle');
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onClick();
    }
  };
  return (
    <Tooltip content={label} position='right'>
      <div
        role='button'
        tabIndex={0}
        aria-label={label}
        aria-current={isActive ? 'page' : undefined}
        onClick={onClick}
        onKeyDown={onKeyDown}
        className={classNames(
          'box-border h-34px w-full flex items-center gap-8px rd-0.5rem cursor-pointer shrink-0 transition-all text-t-primary',
          collapsed ? 'justify-center' : 'justify-start ps-10px pe-8px',
          isActive ? 'bg-fill-3' : 'hover:bg-fill-3 active:bg-fill-4'
        )}
      >
        <span className='size-22px flex items-center justify-center shrink-0 text-t-primary'>
          <Notes theme='outline' size={collapsed ? 20 : 16} fill='currentColor' className='block leading-none' style={{ lineHeight: 0 }} />
        </span>
        {!collapsed && <span className='collapsed-hidden text-t-primary text-14px font-[500] leading-24px'>{label}</span>}
      </div>
    </Tooltip>
  );
}

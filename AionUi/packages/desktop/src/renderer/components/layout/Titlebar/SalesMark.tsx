import React from 'react';

type Props = { size?: number; className?: string };

/** Product mark: the initial of “Sales”, drawn with the current text colour. */
export default function SalesMark({ size = 22, className }: Props) {
  return (
    <svg width={size} height={size} viewBox='0 0 24 24' className={className} aria-hidden='true' focusable='false'>
      <text
        x='12'
        y='12.5'
        textAnchor='middle'
        dominantBaseline='central'
        fontSize='19'
        fontWeight='800'
        fontFamily='-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif'
        fill='currentColor'
      >
        S
      </text>
    </svg>
  );
}

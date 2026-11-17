import type { ReactNode } from 'react';
import { Icon } from '../Icon';

const LINE = 'relative [&+&]:before:absolute [&+&]:before:top-0 [&+&]:before:right-0 [&+&]:before:left-0 [&+&]:before:h-px [&+&]:before:bg-line';

export function Row({ title, sub, meta, right, onClick, label }: {
  title: ReactNode; sub?: ReactNode; meta?: ReactNode; right?: ReactNode; onClick?: () => void; label?: string;
}) {
  const body = (
    <>
      <span className="grid min-w-0 flex-1 gap-0.5">
        <span className="text-t4 font-semibold leading-snug break-words">{title}</span>
        {sub && <span className="text-t6 leading-normal break-words text-ink-3">{sub}</span>}
        {meta && <span className="text-t6 leading-normal text-ink-3">{meta}</span>}
      </span>
      {right}
      {onClick && <Icon name="chev" className="size-5 text-ink-4" />}
    </>
  );
  const cls = `${LINE} flex min-h-[56px] w-full items-center gap-3 py-3 text-left`;
  if (!onClick) return <div className={cls}>{body}</div>;
  return (
    <button type="button" aria-label={label} onClick={onClick}
      className={`${cls} pressable -mx-2 w-[calc(100%+16px)] rounded-tile px-2 [&+&]:before:right-2 [&+&]:before:left-2`}>
      {body}
    </button>
  );
}

export function RowCard({ children }: { children: ReactNode }) {
  return <div className="rounded-card bg-surface px-5 py-1">{children}</div>;
}

import { useL } from '../../i18n/context';
import type { Item } from '../../lib/types';
import { Icon } from '../Icon';
import { H3 } from './H3';

// picks: null = the card is closed, so the options only show what was chosen.
export function Options({ it, picks, busy, onPick }: { it: Item; picks: string[] | null; busy?: boolean; onPick?: (label: string) => void }) {
  const { L } = useL();
  const live = picks !== null;
  const chosen = picks ?? String(it.choice || '').split(' + ');
  return (
    <>
      <H3>{L.label.options}{it.multi && live && <span className="font-medium text-ink-4">{L.label.pickMany}</span>}</H3>
      <div className="grid gap-2" role={it.multi ? 'group' : 'radiogroup'}>
        {it.options.map(o => {
          const on = chosen.includes(o.label);
          return (
            <button key={o.label} type="button" role={it.multi ? 'checkbox' : 'radio'} aria-checked={on}
              disabled={!live || busy} onClick={live ? () => onPick?.(o.label) : undefined}
              className={`flex min-h-[60px] w-full items-center gap-3 rounded-box py-3 pr-4 pl-5 text-left transition-colors disabled:cursor-default ${on ? 'bg-blue-weak' : 'bg-surface-3'} ${!live && !on ? 'opacity-50' : ''}`}>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className={`text-t4 font-semibold leading-snug ${on ? 'text-blue-ink' : ''}`}>{o.label}</span>
                </span>
                {o.desc && <span className="mt-0.5 block text-t5 leading-snug text-ink-2">{o.desc}</span>}
              </span>
              <span className={`flex size-6 flex-none items-center justify-center border-2 transition-colors ${it.multi ? 'rounded-[7px]' : 'rounded-full'} ${on ? 'border-blue bg-blue' : 'border-line-2 bg-surface'}`}>
                <Icon name="check" className={`size-3.5 text-white transition-[opacity,transform] ${on ? 'opacity-100' : 'scale-60 opacity-0'}`} stroke={3} />
              </span>
            </button>
          );
        })}
      </div>
    </>
  );
}

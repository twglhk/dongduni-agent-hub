import { IS_MAC } from '../lib/keys';

// Only where there is a keyboard and a mouse; phones (touch or narrow) hide it.
export function KbdHint() {
  return <span aria-hidden="true" className="ml-1.5 whitespace-nowrap text-t6 font-semibold tracking-wide opacity-70 max-[759px]:hidden [@media(hover:none),(pointer:coarse)]:hidden">{IS_MAC ? '⌘↵' : 'Ctrl ↵'}</span>;
}

import { IS_MAC } from '../lib/keys';

export function KbdHint() {
  return <span aria-hidden="true" className="ml-1.5 whitespace-nowrap text-t6 font-semibold tracking-wide opacity-70 max-[759px]:hidden [@media(hover:none),(pointer:coarse)]:hidden">{IS_MAC ? '⌘↵' : 'Ctrl ↵'}</span>;
}

import type { Dict } from '../../i18n/dict';
import type { SecTab } from '../../lib/prefs';

export function secName(L: Dict, tab: SecTab, id: string): string {
  if (tab === 'hub') return id === 'asks' ? L.sec.asks : L.sec.yours;
  return id === 'lanes' ? L.sec.lanes : id === 'tasks' ? L.sec.tasks : L.sec.reports;
}

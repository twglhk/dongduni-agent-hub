import { useL } from '../../i18n/context';
import { docUrl } from '../../lib/engine';
import { openDoc } from '../../state/docSheet';

export function DocChip({ eid, path, label, title }: { eid: string; path?: string | null; label: string; title?: string }) {
  const { L } = useL();
  return (
    <button type="button" disabled={!path} title={path || L.eg.noDoc}
      onClick={() => path && openDoc({ title: title || label, path, url: docUrl(eid, path) })}
      className="inline-flex h-6 items-center rounded-chip bg-blue-weak px-2 text-t6 font-semibold whitespace-nowrap text-blue-ink disabled:cursor-default disabled:opacity-60">
      {label}
    </button>
  );
}

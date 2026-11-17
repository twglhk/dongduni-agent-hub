import { markdown } from '../lib/markdown';

export function Prose({ text, className = '', doc = false }: { text?: string | null; className?: string; doc?: boolean }) {
  return <div className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: markdown(text, doc) }} />;
}

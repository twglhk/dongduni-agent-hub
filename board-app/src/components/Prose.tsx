import { markdown } from '../lib/markdown';

// markdown() escapes every server string before it adds a tag, so this HTML is safe to set.
export function Prose({ text, className = '', doc = false }: { text?: string | null; className?: string; doc?: boolean }) {
  return <div className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: markdown(text, doc) }} />;
}

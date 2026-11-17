import type { KeyboardEvent } from 'react';

const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
export const IS_MAC = /Mac|iPhone|iPad|iPod/.test(nav.userAgentData?.platform || navigator.platform || navigator.userAgent);
export const SEND_KEYS = IS_MAC ? 'Meta+Enter' : 'Control+Enter';

// Not while a Korean/Japanese/Chinese word is still being composed (isComposing, keyCode 229).
export const isSendKey = (e: KeyboardEvent) =>
  e.key === 'Enter' && (IS_MAC ? e.metaKey : e.ctrlKey) && !e.shiftKey && !e.altKey
  && !e.nativeEvent.isComposing && e.keyCode !== 229;

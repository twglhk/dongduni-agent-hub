import type { ProcLib } from '../lib/procTypes';
import { usePoll } from './usePoll';

const MS = 15000;

export const useProcesses = (on = true) => usePoll<ProcLib>(on ? 'api/processes' : null, MS);

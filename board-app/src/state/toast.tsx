import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';

type Show = (msg: string, err?: boolean) => void;
const ToastContext = createContext<Show>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [t, setT] = useState<{ msg: string; err: boolean; on: boolean }>({ msg: '', err: false, on: false });
  const timer = useRef<number>(0);
  const show = useCallback<Show>((msg, err = false) => {
    setT({ msg, err, on: true });
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setT(x => ({ ...x, on: false })), 2600);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div role="status" aria-live="polite"
        className={`fixed left-1/2 z-[60] w-max max-w-[calc(100vw-40px)] -translate-x-1/2 rounded-box px-5 py-3.5 text-center text-t5 font-medium leading-snug shadow-[0_8px_24px_rgba(0,0,0,.18)] transition-[opacity,transform] duration-200 pointer-events-none bottom-[calc(var(--nav-h)+var(--chat-h)+26px)] in-[.sheet-open]:bottom-[calc(150px+env(safe-area-inset-bottom))] in-[.locked]:bottom-[calc(24px+env(safe-area-inset-bottom))] ${t.err ? 'bg-red text-white' : 'bg-toast text-toast-fg'} ${t.on ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}>
        {t.msg}
      </div>
    </ToastContext.Provider>
  );
}

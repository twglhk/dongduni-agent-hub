import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { LangProvider } from './i18n/LangProvider';
import { BoardProvider } from './state/board';
import { ToastProvider } from './state/toast';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BoardProvider>
      <LangProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </LangProvider>
    </BoardProvider>
  </StrictMode>,
);

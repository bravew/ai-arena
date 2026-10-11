import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import '../theme/tokens.css';
import './shell.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('Missing #root app element');

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import { useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { useBundleState } from './state';

const navigation = [
  { path: '/', label: 'Overview', end: true },
  { path: '/leaderboard', label: 'Leaderboard' },
  { path: '/pareto', label: 'Pareto' },
  { path: '/matrix', label: 'Matrix' },
  { path: '/compare', label: 'Compare' },
  { path: '/trace', label: 'Trace' },
  { path: '/run-diff', label: 'Run diff' },
  { path: '/sessions', label: 'Sessions' },
  { path: '/kit-effect', label: 'Kit effect' },
  { path: '/live', label: 'Live' },
  { path: '/ops', label: 'Ops' },
];

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { loadBundle, setLoadError } = useBundleState();
  const location = useLocation();
  const pageName = navigation.find((item) => item.path === location.pathname)?.label ?? 'Not found';

  return (
    <div className="app-shell" data-sidebar-collapsed={collapsed}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand-row">
          <span className="brand-mark" aria-hidden="true">A</span>
          {!collapsed && <span className="brand-name">AI Arena</span>}
          <button
            className="icon-button sidebar-toggle"
            type="button"
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            onClick={() => setCollapsed((value) => !value)}
          >
            <span aria-hidden="true">{collapsed ? '›' : '‹'}</span>
          </button>
        </div>
        <nav className="nav-list">
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              className={({ isActive }) => `nav-link${isActive ? ' is-active' : ''}`}
              aria-label={collapsed ? item.label : undefined}
            >
              <span className="nav-glyph" aria-hidden="true">{glyphFor(item.path)}</span>
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="connection-dot" aria-hidden="true" />
          {!collapsed && <span>Fixture bundle</span>}
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div>
            <div className="eyebrow">Workspace / Fixture run</div>
            <h1>{pageName}</h1>
          </div>
          <div className="topbar-actions">
            <input
              ref={fileInput}
              className="visually-hidden"
              type="file"
              accept="application/json,.json"
              aria-label="Choose report bundle JSON"
              onChange={async (event) => {
                const file = event.currentTarget.files?.[0];
                if (!file) return;
                try {
                  loadBundle(JSON.parse(await file.text()) as unknown);
                } catch (error) {
                  setLoadError(error instanceof Error ? `invalid JSON: ${error.message}` : 'invalid JSON');
                }
                event.currentTarget.value = '';
              }}
            />
            <button className="import-button" type="button" onClick={() => fileInput.current?.click()}>Import bundle <span aria-hidden="true">↑</span></button>
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="statusbar">
          <span><i className="status-swatch" aria-hidden="true" /> Fixture bundle validated</span>
          <span>Schema v1 <span aria-hidden="true">·</span> Static preview</span>
        </footer>
      </div>
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('arena-theme');
    return stored === 'dark' || stored === 'light' ? stored : 'light';
  });
  const flip = () => {
    const next = theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('arena-theme', next);
    setTheme(next);
  };
  return (
    <button className="theme-button" type="button" onClick={flip} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}>
      <span aria-hidden="true">{theme === 'light' ? '◐' : '☼'}</span>
      <span>{theme === 'light' ? 'Light' : 'Dark'}</span>
    </button>
  );
}

function glyphFor(path: string): string {
  const glyphs: Record<string, string> = {
    '/': '⌂',
    '/leaderboard': '≡',
    '/pareto': '⌁',
    '/matrix': '▦',
    '/compare': '◫',
    '/trace': '⌁',
    '/run-diff': '⇄',
    '/sessions': '◷',
    '/kit-effect': '◇',
    '/live': '◉',
    '/ops': '⚙',
  };
  return glyphs[path] ?? '·';
}

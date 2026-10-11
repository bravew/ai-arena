import { useContext, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, createHashRouter, RouterProvider } from 'react-router';
import { validateBundle, validateEventStream } from '../lib/schema';
import type { RunEvent } from '../lib/schema';
import { schemaExample } from '../lib/schema/fixture';
import { AppLayout } from './routes';
import { HomePage, PlaceholderPage } from './pages';
import { SessionsView } from '../views/sessions/SessionsView';
import { KitEffectView } from '../views/kit-effect/KitEffectView';
import { BundleContext, useBundleState } from './state';
import { readStaticBundle } from './static-bundle';
import { CompareView } from '../views/compare/CompareView';
import { TraceView } from '../views/trace/TraceView';
import { RunDiffView } from '../views/rundiff/RunDiffView';
import { LiveView } from '../views/live/LiveView';
import { OpsView } from '../views/ops/OpsView';

function CompareRoute() { const { bundle } = useBundleState(); return bundle ? <CompareView bundle={bundle} /> : <PlaceholderPage title="Compare trials" />; }
function TraceRoute() { const { bundle } = useBundleState(); return bundle ? <TraceView bundle={bundle} /> : <PlaceholderPage title="Trace" />; }
function RunDiffRoute() { const { bundle } = useBundleState(); return bundle ? <RunDiffView bundle={bundle} /> : <PlaceholderPage title="Run diff" />; }
function LiveRoute() {
  const { bundle, events } = useBundleState();
  return <LiveView bundle={bundle} replayEvents={events} />;
}

const routeConfig = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'leaderboard', element: <PlaceholderPage title="Leaderboard" /> },
      { path: 'pareto', element: <PlaceholderPage title="Pareto frontier" /> },
      { path: 'matrix', element: <PlaceholderPage title="Task matrix" /> },
      { path: 'compare', element: <CompareRoute /> },
      { path: 'trace', element: <TraceRoute /> },
      { path: 'run-diff', element: <RunDiffRoute /> },
      { path: 'sessions', element: <SessionsPage /> },
      { path: 'kit-effect', element: <KitEffectPage /> },
      { path: 'live', element: <LiveRoute /> },
      { path: 'ops', element: <OpsView /> },
      { path: '*', element: <PlaceholderPage title="Page not found" /> },
    ],
  },
];

const router = window.location.protocol === 'file:'
  ? createHashRouter(routeConfig)
  : createBrowserRouter(routeConfig);

function SessionsPage() {
  const { bundle } = useContext(BundleContext) ?? {};
  return bundle ? <SessionsView bundle={bundle} /> : <PlaceholderPage title="Sessions" />;
}

function KitEffectPage() {
  const { bundle } = useContext(BundleContext) ?? {};
  return bundle ? <KitEffectView bundle={bundle} /> : <PlaceholderPage title="Kit effect" />;
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });

export function App() {
  const [staticData] = useState(readStaticBundle);
  const [validation, setValidation] = useState(() => staticData?.error
    ? { ok: false as const, issues: [{ path: '', message: staticData.error }] }
    : validateBundle(staticData?.input ?? schemaExample));
  const [events, setEvents] = useState<RunEvent[] | undefined>(() => {
    if (!staticData) return undefined;
    const parsed = validateEventStream(
      staticData.events.map((event) => JSON.stringify(event)).join('\n'),
    );
    return parsed.ok ? parsed.value : [];
  });
  const bundle = validation.ok ? validation.value : undefined;
  const context = useMemo(() => ({
    bundle,
    validation,
    events,
    loadEvents: (input: unknown) => {
      const parsed = Array.isArray(input)
        ? input.map((event) => JSON.stringify(event)).join('\n')
        : '';
      const result = validateEventStream(parsed);
      setEvents(result.ok ? result.value : undefined);
    },
    loadBundle: (input: unknown) => setValidation(validateBundle(input)),
    setLoadError: (message: string) => setValidation({ ok: false, issues: [{ path: '', message }] }),
  }), [bundle, events, validation]);

  return (
    <QueryClientProvider client={queryClient}>
      <BundleContext.Provider value={context}>
        <RouterProvider router={router} />
      </BundleContext.Provider>
    </QueryClientProvider>
  );
}

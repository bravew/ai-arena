import { useContext, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { schemaExample } from '../lib/schema/fixture';
import { validateBundle } from '../lib/schema';
import { AppLayout } from './routes';
import { HomePage, PlaceholderPage } from './pages';
import { SessionsView } from '../views/sessions/SessionsView';
import { KitEffectView } from '../views/kit-effect/KitEffectView';
import { BundleContext, useBundleState } from './state';
import { CompareView } from '../views/compare/CompareView';
import { TraceView } from '../views/trace/TraceView';
import { RunDiffView } from '../views/rundiff/RunDiffView';
import { LiveView } from '../views/live/LiveView';
import { OpsView } from '../views/ops/OpsView';

function CompareRoute() { const { bundle } = useBundleState(); return bundle ? <CompareView bundle={bundle} /> : <PlaceholderPage title="Compare trials" />; }
function TraceRoute() { const { bundle } = useBundleState(); return bundle ? <TraceView bundle={bundle} /> : <PlaceholderPage title="Trace" />; }
function RunDiffRoute() { const { bundle } = useBundleState(); return bundle ? <RunDiffView bundle={bundle} /> : <PlaceholderPage title="Run diff" />; }
function LiveRoute() {
  const { bundle, validation } = useBundleState();
  return <LiveView bundle={bundle ?? (validation?.ok === false ? schemaExample : undefined)} />;
}

const router = createBrowserRouter([
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
]);

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
  const [validation, setValidation] = useState(() => validateBundle(schemaExample));
  const bundle = validation.ok ? validation.value : undefined;
  const context = useMemo(() => ({
    bundle,
    validation,
    loadBundle: (input: unknown) => setValidation(validateBundle(input)),
    setLoadError: (message: string) => setValidation({ ok: false, issues: [{ path: '', message }] }),
  }), [bundle, validation]);

  return (
    <QueryClientProvider client={queryClient}>
      <BundleContext.Provider value={context}>
        <RouterProvider router={router} />
      </BundleContext.Provider>
    </QueryClientProvider>
  );
}

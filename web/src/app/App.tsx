import { useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { schemaExample } from '../lib/schema/fixture';
import { validateBundle } from '../lib/schema';
import { AppLayout } from './routes';
import { HomePage, PlaceholderPage } from './pages';
import { BundleContext, useBundleState } from './state';
import { LiveView } from '../views/live/LiveView';
import { OpsView } from '../views/ops/OpsView';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'leaderboard', element: <PlaceholderPage title="Leaderboard" /> },
      { path: 'pareto', element: <PlaceholderPage title="Pareto frontier" /> },
      { path: 'matrix', element: <PlaceholderPage title="Task matrix" /> },
      { path: 'compare', element: <PlaceholderPage title="Compare trials" /> },
      { path: 'trace', element: <PlaceholderPage title="Trace" /> },
      { path: 'run-diff', element: <PlaceholderPage title="Run diff" /> },
      { path: 'sessions', element: <PlaceholderPage title="Sessions" /> },
      { path: 'kit-effect', element: <PlaceholderPage title="Kit effect" /> },
      { path: 'live', element: <LiveRoute /> },
      { path: 'ops', element: <OpsView /> },
      { path: '*', element: <PlaceholderPage title="Page not found" /> },
    ],
  },
]);

function LiveRoute() {
  const { bundle, validation } = useBundleState();
  return <LiveView bundle={bundle ?? (validation?.ok === false ? schemaExample : undefined)} />;
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

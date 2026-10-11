export interface StaticBundleState {
  input: unknown;
  events: unknown[];
  error?: string;
}

export function readStaticBundle(): StaticBundleState | undefined {
  if (window.location.protocol !== 'file:') return undefined;
  const source = document.getElementById('arena-static-data');
  if (!source?.textContent) {
    return { input: undefined, events: [], error: 'Static run data is missing.' };
  }
  try {
    return JSON.parse(source.textContent) as StaticBundleState;
  } catch (error) {
    return {
      input: undefined,
      events: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

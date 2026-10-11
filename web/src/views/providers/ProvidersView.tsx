import { useEffect, useState } from 'react';
import { fetchProviders, testProvider, type ProviderStatus, type ProvidersSnapshot } from './http-source';
import './providers.css';

const statusLabels: Record<string, string> = {
  ok: 'Ready',
  configured: 'Configured',
  disabled: 'Disabled',
  not_installed: 'CLI not installed',
  auth_required: 'Sign-in required',
  unauthenticated: 'Sign-in required',
  incompatible_version: 'Update required',
  unsupported_model: 'Model unavailable',
  protocol_failure: 'Could not read CLI status',
  permission_failure: 'Permission denied',
  unknown: 'Status unavailable',
};

export function ProvidersView({ load = fetchProviders, runTest = testProvider }: {
  load?: typeof fetchProviders;
  runTest?: typeof testProvider;
}) {
  const [snapshot, setSnapshot] = useState<ProvidersSnapshot>();
  const [error, setError] = useState('');
  const [testing, setTesting] = useState<string>();
  const [testMessages, setTestMessages] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    void load().then((data) => { if (active) setSnapshot(data); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : 'loading providers failed'); });
    return () => { active = false; };
  }, [load]);

  async function test(id: string) {
    if (testing) return;
    setTesting(id);
    setTestMessages((previous) => ({ ...previous, [id]: '' }));
    try {
      const result = await runTest(id);
      setTestMessages((previous) => ({ ...previous, [id]: result.message ?? result.status ?? 'Connection test completed.' }));
    } catch (cause) {
      setTestMessages((previous) => ({ ...previous, [id]: cause instanceof Error ? cause.message : 'Connection test failed.' }));
    } finally {
      setTesting(undefined);
    }
  }

  return <section className="providers-view" aria-labelledby="providers-title">
    <header className="providers-heading"><div><div className="eyebrow">ONBOARDING</div><h2 id="providers-title">Providers</h2><p>Check configured providers and find the next setup step.</p></div></header>
    {error && <p role="alert" className="providers-error">{error}</p>}
    {!snapshot && !error && <p role="status">Loading provider status…</p>}
    {snapshot && <>
      <div className="panel providers-instructions"><h3>Add a provider</h3><p>{snapshot.instructions.note}</p><code>{snapshot.instructions.subscription}</code><code>{snapshot.instructions.api}</code></div>
      <div className="providers-list">{snapshot.providers.map((provider) => <ProviderCard key={provider.id} provider={provider} onTest={() => void test(provider.id)} testing={testing === provider.id} message={testMessages[provider.id]} />)}</div>
      {snapshot.providers.length === 0 && <p>No providers are configured.</p>}
    </>}
  </section>;
}

function ProviderCard({ provider, onTest, testing, message }: {
  provider: ProviderStatus;
  onTest: () => void;
  testing: boolean;
  message?: string;
}) {
  const label = statusLabels[provider.status] ?? provider.status.replaceAll('_', ' ');
  return <article className="panel provider-card">
    <div className="provider-card-heading"><div><div className="eyebrow">{provider.kind}{provider.plan ? ` · ${provider.plan}` : ''}</div><h3>{provider.id}</h3></div><span className={`provider-status provider-status-${provider.status}`} role="status">{label}</span></div>
    <dl className="provider-details">
      <div><dt>Runtime</dt><dd>{provider.runtime}</dd></div>
      {provider.vendor && <div><dt>CLI</dt><dd>{provider.cli_found ? `${provider.vendor} found` : 'Not found'}{provider.version ? ` · ${provider.version}` : ''}</dd></div>}
      {provider.vendor && <div><dt>Sign-in</dt><dd>{provider.sign_in === 'stored' ? 'Saved in Arena' : provider.sign_in === 'cli_discovered' ? 'Existing CLI sign-in found' : provider.auth ?? 'unknown'}</dd></div>}
      {provider.key_envs.length > 0 && <div><dt>API key</dt><dd>Environment variable name: {provider.key_envs.join(', ')}</dd></div>}
      {provider.detail && <div><dt>Details</dt><dd>{provider.detail}</dd></div>}
    </dl>
    {provider.hint && <p className="provider-hint">Next step: <code>{provider.hint}</code></p>}
    {provider.enabled && <div className="provider-test"><button type="button" disabled={testing} onClick={onTest}>{testing ? 'Testing…' : 'Test connection'}</button><span>This sends one capped model request.</span></div>}
    {message && <p className="provider-test-result" role="status">{message}</p>}
  </article>;
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchProviders, fetchSettingsToken, mutateSettings, protocols, testProvider, type ProviderStatus, type ProvidersSnapshot, type TestResult, type TestSelection, type TestTokens } from './http-source';
import { ProviderForm, errorMessage, type SettingsMutation } from './settings-forms';
import { AccountSettings } from './AccountSettings';
import { RunLauncher } from './RunLauncher';
import type { LaunchApi } from './http-source';
import { KeySettings } from './KeySettings';
import { ModelSettings } from './ModelSettings';
import './providers.css';
const statusLabels: Record<string, string> = {
  ok: 'Ready', configured: 'Configured', disabled: 'Disabled', not_installed: 'CLI not installed',
  auth_required: 'Sign-in required', unauthenticated: 'Sign-in required', incompatible_version: 'Update required',
  unsupported_model: 'Model unavailable', protocol_failure: 'Could not read CLI status', permission_failure: 'Permission denied', unknown: 'Status unavailable',
};
export function ProvidersView({ load = fetchProviders, runTest = testProvider, launch }: { load?: typeof fetchProviders; runTest?: typeof testProvider; launch?: LaunchApi }) {
  const isStatic = window.location.protocol === 'file:' || !!document.getElementById('arena-static-data');
  const [snapshot, setSnapshot] = useState<ProvidersSnapshot>();
  const [csrf, setCsrf] = useState('');
  const [error, setError] = useState(() => isStatic ? 'Provider settings are read-only in this static export. Open the local Arena server to manage providers.' : '');
  const [adding, setAdding] = useState(false);
  const mounted = useRef(false);
  const apply = useCallback(async (data: ProvidersSnapshot) => {
    if (!mounted.current) return;
    setSnapshot(data); setError('');
    if (data.capabilities?.settings) {
      try { const token = await fetchSettingsToken(); if (mounted.current) setCsrf(token); }
      catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    } else setCsrf('');
  }, []);
  const refresh = useCallback(async () => { if (!isStatic) await apply(await load()); }, [apply, isStatic, load]);
  useEffect(() => {
    mounted.current = true;
    if (isStatic) return () => { mounted.current = false; };
    void load().then((data) => apply(data)).catch((cause: unknown) => { if (mounted.current) setError(errorMessage(cause)); });
    return () => { mounted.current = false; };
  }, [apply, isStatic, load]);
  const writable = !!snapshot?.capabilities?.settings && !!csrf;
  const mutate: SettingsMutation = async (path, body, reload = true) => {
    if (!writable) throw new Error('Provider settings are read-only here.');
    const result = await mutateSettings(path, body, csrf);
    if (reload) {
      try { await refresh(); }
      catch (cause) { setError(`Saved, but refreshing provider status failed. ${errorMessage(cause)}`); }
    }
    return result;
  };
  return <section className="providers-view" aria-labelledby="providers-title">
    <header className="providers-heading"><div><div className="eyebrow">ONBOARDING</div><h2 id="providers-title">Providers</h2><p>Manage providers, credentials, subscription sign-ins and catalog models.</p></div></header>
    {error && <p role="alert" className="providers-error">{error}</p>}
    {!snapshot && !error && <p role="status">Loading provider status…</p>}
    <div className="provider-actions"><button type="button" onClick={() => void refresh().catch((cause) => setError(errorMessage(cause)))}>Refresh providers</button>{writable && <button type="button" onClick={() => setAdding(true)}>Add provider</button>}</div>
    {snapshot && <>
      {snapshot.capabilities?.settings ? <p className="providers-local">Local settings home: <code>{snapshot.settings_home}</code>{!csrf && ' · Settings access unavailable'}</p>
        : <div className="panel providers-instructions"><h3>Provider settings are read-only</h3><p>Open the local Arena server to manage providers.</p><p>{snapshot.instructions.note}</p><code>{snapshot.instructions.subscription}</code><code>{snapshot.instructions.api}</code></div>}
      {adding && writable && <ProviderForm revision={snapshot.revision ?? ''} mutate={mutate} onClose={() => setAdding(false)} />}
      {writable && snapshot.capabilities?.launch && <RunLauncher csrf={csrf} api={launch} />}
      <div className="providers-list">{snapshot.providers.map((provider) => <ProviderCard key={provider.id} provider={provider} snapshot={snapshot} writable={writable} mutate={mutate} refresh={refresh} runTest={runTest} csrf={csrf} />)}</div>
      {snapshot.providers.length === 0 && <p>No providers are configured.</p>}
    </>}
  </section>;
}
function ProviderCard({ provider, snapshot, writable, mutate, refresh, runTest, csrf }: {
  provider: ProviderStatus; snapshot: ProvidersSnapshot; writable: boolean; mutate: SettingsMutation; refresh: () => Promise<void>; runTest: typeof testProvider; csrf: string;
}) {
  const [editing, setEditing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState('');
  const [outcome, setOutcome] = useState<TestResult | null>(null);
  const [selection, setSelection] = useState<TestSelection>({});
  const label = statusLabels[provider.status] ?? provider.status.replaceAll('_', ' ');
  async function test() {
    setTesting(true); setMessage(''); setOutcome(null);
    try {
      const result = Object.keys(selection).length || csrf ? await runTest(provider.id, selection, csrf || undefined) : await runTest(provider.id);
      setOutcome(result);
      setMessage(result.message ?? result.error ?? result.status ?? 'Connection test completed.');
    } catch (cause) { setMessage(errorMessage(cause)); } finally { setTesting(false); }
  }
  const testProtocolOptions = protocols.filter((p) => provider.config?.apis?.[p] || provider.models?.some((m) => m.protocols.includes(p)));
  return <article className="panel provider-card" aria-label={`${provider.id} provider`}>
    <div className="provider-card-heading"><div><div className="eyebrow">{provider.kind}{provider.plan ? ` · ${provider.plan}` : ''}</div><h3>{provider.id}</h3></div><span className={`provider-status provider-status-${provider.status}`} role="status">{label}</span></div>
    <dl className="provider-details">
      <div><dt>Runtime</dt><dd>{provider.runtime}</dd></div>
      {provider.vendor && <div><dt>CLI</dt><dd>{provider.cli_found ? `${provider.vendor} found` : 'Not found'}{provider.version ? ` · ${provider.version}` : ''}</dd></div>}
      {provider.vendor && <div><dt>Sign-in</dt><dd>{provider.sign_in === 'stored' ? 'Saved in Arena' : provider.sign_in === 'cli_discovered' ? 'Existing CLI sign-in found' : provider.auth ?? 'unknown'}</dd></div>}
      {provider.key_envs.length > 0 && <div><dt>API key</dt><dd>Environment variable name: {provider.key_envs.join(', ')}</dd></div>}
      {provider.detail && <div><dt>Details</dt><dd>{provider.detail}</dd></div>}
    </dl>
    {provider.hint && <p className="provider-hint">Next step: <code>{provider.hint}</code></p>}
    {writable && provider.config && <button type="button" onClick={() => setEditing(true)}>Edit provider {provider.id}</button>}
    {writable && editing && provider.config && <ProviderForm config={provider.config} revision={snapshot.revision ?? ''} mutate={mutate} onClose={() => setEditing(false)} />}
    {writable && provider.kind === 'api' && <KeySettings provider={provider} mutate={mutate} />}
    {writable && provider.kind === 'subscription' && <AccountSettings provider={provider} mutate={mutate} refresh={refresh} />}
    {(provider.models || writable) && <ModelSettings provider={provider} revision={snapshot.catalog_revision ?? ''} writable={writable} mutate={mutate} />}
    {provider.enabled && <section className="provider-test" aria-label={`${provider.id} connection test`}>
      {writable && <fieldset className="provider-form-fields"><legend>Connection test selection</legend>
        {!!provider.models?.length && <fieldset className="provider-choice"><legend>Test model</legend><label><input type="radio" name={`${provider.id}-test-model`} checked={!selection.model} onChange={() => setSelection({ ...selection, model: undefined })} />Default model</label>{provider.models.map((model) => <label key={model.ref}><input type="radio" name={`${provider.id}-test-model`} checked={selection.model === model.ref} onChange={() => setSelection({ ...selection, model: model.ref })} />{model.ref}</label>)}</fieldset>}
        {!!testProtocolOptions.length && <fieldset className="provider-choice"><legend>Test protocol</legend><label><input type="radio" name={`${provider.id}-test-protocol`} checked={!selection.protocol} onChange={() => setSelection({ ...selection, protocol: undefined })} />Default protocol</label>{testProtocolOptions.map((p) => <label key={p}><input type="radio" name={`${provider.id}-test-protocol`} checked={selection.protocol === p} onChange={() => setSelection({ ...selection, protocol: p })} />{p}</label>)}</fieldset>}
        {!!provider.config?.keys?.length && <fieldset className="provider-choice"><legend>Test key</legend><label><input type="radio" name={`${provider.id}-test-key`} checked={!selection.key_id} onChange={() => setSelection({ ...selection, key_id: undefined })} />Default key</label>{provider.config.keys.map((key) => <label key={key.id}><input type="radio" name={`${provider.id}-test-key`} checked={selection.key_id === key.id} onChange={() => setSelection({ ...selection, key_id: key.id })} />{key.id}</label>)}</fieldset>}
      </fieldset>}
      <button type="button" disabled={testing} onClick={() => void test()}>{testing ? 'Testing…' : 'Test connection'}</button><span>{provider.kind === 'subscription' ? 'This sends one real request through the vendor CLI, with a 60-second limit. It uses subscription quota.' : 'This sends one capped model request.'}</span>
    </section>}
    {message && <div className={`provider-test-result${outcome ? (outcome.ok ? ' provider-test-ok' : ' provider-test-failed') : ' provider-test-failed'}`} role="status">
      {outcome && <strong>{outcome.ok ? 'Test passed' : 'Test failed'}</strong>}
      <p>{message}</p>
      {outcome?.tokens && <p className="provider-test-usage">Usage reported by {outcome.source === 'cli_reported' ? 'the vendor CLI' : 'the provider'}: {formatTokens(outcome.tokens)}</p>}
    </div>}
  </article>;
}

function formatTokens(tokens: TestTokens): string {
  const parts: [string, number | undefined][] = [['input', tokens.input], ['output', tokens.output], ['reasoning', tokens.reasoning], ['cache read', tokens.cache_read], ['cache write', tokens.cache_write]];
  return parts.filter(([, n]) => n !== undefined).map(([label, n]) => `${n!.toLocaleString('en-US')} ${label}`).join(' · ');
}

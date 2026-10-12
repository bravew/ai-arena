import { useId, useState } from 'react';
import { protocols, safeMessage, type ProviderConfig, type Protocol, type ProviderKey } from './http-source';

export type SettingsMutation = (path: string, body: Record<string, unknown>, refresh?: boolean) => Promise<Record<string, unknown>>;
export const providerPath = (id: string) => `/api/providers/${encodeURIComponent(id)}`;
export const errorMessage = (cause: unknown) => safeMessage(cause instanceof Error ? cause.message : 'Provider operation failed.');
export function Choice<T extends string>({ label, value, values, onChange }: { label: string; value: T; values: readonly T[]; onChange: (value: T) => void }) {
  const name = useId();
  return <fieldset className="provider-choice"><legend>{label}</legend>{values.map((item) => <label key={item}><input type="radio" name={name} checked={value === item} onChange={() => onChange(item)} />{item}</label>)}</fieldset>;
}
export function ProtocolFields({ value, onChange, options = protocols }: { value: Protocol[]; onChange: (value: Protocol[]) => void; options?: readonly Protocol[] }) {
  return <fieldset className="provider-choice"><legend>Protocols</legend>{options.map((protocol) => <label key={protocol}><input type="checkbox" checked={value.includes(protocol)} onChange={(e) => onChange(e.target.checked ? [...value, protocol] : value.filter((p) => p !== protocol))} />{protocol}</label>)}</fieldset>;
}
const identifier = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const environment = /^(?:[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})$/;
export function endpointProblem(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.username || url.password) return 'Remove credentials from the endpoint URL.';
    if (url.protocol === 'https:') return null;
    const host = url.hostname;
    if (url.protocol === 'http:' && (host === 'localhost' || host === '::1' || /^127\./.test(host))) return null;
    return 'Use an absolute HTTPS endpoint, or HTTP on loopback.';
  } catch { return 'Enter an absolute endpoint URL.'; }
}
export function ProviderForm({ config, revision, mutate, onClose }: {
  config?: ProviderConfig; revision: string; mutate: SettingsMutation; onClose: () => void;
}) {
  const [base] = useState(config);
  const [baseRevision] = useState(revision);
  const [id, setId] = useState(config?.id ?? '');
  const [kind, setKind] = useState<'api' | 'subscription'>(config?.subscription ? 'subscription' : 'api');
  const [enabled, setEnabled] = useState(config?.enabled ?? true);
  const [concurrency, setConcurrency] = useState(String(config?.limits?.concurrency ?? ''));
  const [apis, setApis] = useState(config?.apis ?? {});
  const [keys, setKeys] = useState<ProviderKey[]>(config?.keys ?? [{ id: 'main', env: '' }]);
  const [vendor, setVendor] = useState(config?.subscription?.vendor === 'chatgpt' ? 'chatgpt' : 'claude');
  const [plan, setPlan] = useState(config?.subscription?.plan ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const builtin = base?.kind === 'mock' || base?.kind === 'cassette';
  async function submit() {
    setError('');
    if (!identifier.test(id.trim())) { setError('Provider ID must start with a letter or digit and use only letters, digits, dots, underscores or dashes.'); return; }
    if (concurrency && (!Number.isInteger(Number(concurrency)) || Number(concurrency) < 1)) { setError('Concurrency must be a positive integer.'); return; }
    if (!builtin && kind === 'api' && (!keys.length || keys.some((key) => !identifier.test(key.id.trim()) || !environment.test(key.env.trim())))) {
      setError('Each API key needs an identifier and an environment variable name. Save the key value separately after saving the provider.'); return;
    }
    if (!builtin && kind === 'api' && !protocols.some((p) => apis[p]?.trim())) { setError('Add at least one API endpoint.'); return; }
    if (!builtin && kind === 'subscription' && !plan.trim()) { setError('Enter the subscription plan.'); return; }
    const endpoints: Record<string, string> = {};
    for (const p of protocols) {
      const value = apis[p]?.trim();
      if (!value) continue;
      const problem = endpointProblem(value);
      if (problem) { setError(`${p} endpoint: ${problem}`); return; }
      endpoints[p] = value;
    }
    const limits = { ...base?.limits };
    if (concurrency) limits.concurrency = Number(concurrency); else delete limits.concurrency;
    const provider: ProviderConfig = { ...base, id: id.trim(), enabled, limits };
    if (builtin) { delete provider.apis; delete provider.keys; delete provider.subscription; }
    else {
      provider.apis = endpoints;
      provider.keys = kind === 'api' ? keys.map((key) => ({ ...key, id: key.id.trim(), env: key.env.trim() })) : [];
      provider.subscription = kind === 'subscription'
        ? { ...base?.subscription, vendor, plan: plan.trim(), runtime: 'local-cli', import: base?.subscription?.import ?? 'own-sign-in' }
        : null;
    }
    setBusy(true);
    try { await mutate(base ? `${providerPath(base.id)}/config` : '/api/providers', { revision: baseRevision, provider }); onClose(); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <form className="provider-form panel" aria-label={base ? `Edit ${base.id}` : 'Add provider'} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    <h3>{base ? `Edit ${base.id}` : 'Add provider'}</h3>
    {error && <p role="alert" className="providers-error">{error}</p>}
    <fieldset disabled={busy} className="provider-form-fields">
      <label>Provider ID<input value={id} disabled={!!base} onChange={(e) => setId(e.target.value)} required /></label>
      {!builtin && <Choice label="Provider kind" value={kind} values={['api', 'subscription']} onChange={setKind} />}
      <label className="provider-checkbox"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Enabled</label>
      <label>Concurrency<input type="number" min="1" step="1" value={concurrency} onChange={(e) => setConcurrency(e.target.value)} placeholder="Provider default" /></label>
      {!builtin && <>
        <fieldset className="provider-form-fields"><legend>API endpoints</legend><p>Use HTTPS, or HTTP on loopback for a local provider. Endpoints are optional for a local CLI subscription.</p>{protocols.map((p) => <label key={p}>{p} endpoint<input type="url" value={apis[p] ?? ''} onChange={(e) => setApis({ ...apis, [p]: e.target.value })} placeholder="https://…" /></label>)}</fieldset>
        {kind === 'api' ? <fieldset className="provider-form-fields"><legend>Key references</legend>{keys.map((key, index) => <div className="provider-key-row" key={index}>
          <label>Key ID {index + 1}<input value={key.id} required onChange={(e) => setKeys(keys.map((k, i) => i === index ? { ...k, id: e.target.value } : k))} /></label>
          <label>Environment variable {index + 1}<input value={key.env} required autoComplete="off" onChange={(e) => setKeys(keys.map((k, i) => i === index ? { ...k, env: e.target.value } : k))} /></label>
          <button type="button" onClick={() => setKeys(keys.filter((_, i) => i !== index))}>Remove key {index + 1}</button>
        </div>)}<button type="button" onClick={() => setKeys([...keys, { id: '', env: '' }])}>Add key reference</button><p>Only references are saved here. Key values are entered separately.</p></fieldset>
          : <><Choice label="Subscription vendor" value={vendor} values={['claude', 'chatgpt']} onChange={setVendor} /><label>Plan<input value={plan} required onChange={(e) => setPlan(e.target.value)} /></label><p>New subscription settings run through the vendor’s local CLI.</p></>}
      </>}
      <div className="provider-actions"><button type="submit">{busy ? 'Saving…' : 'Save provider'}</button><button type="button" onClick={onClose}>Cancel</button></div>
    </fieldset>
  </form>;
}

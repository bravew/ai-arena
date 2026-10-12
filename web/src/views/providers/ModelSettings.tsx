import { useState } from 'react';
import { parseDiscovery, protocols, type CatalogModel, type DiscoveredModel, type Protocol, type ProviderStatus } from './http-source';
import { errorMessage, ProtocolFields, providerPath, type SettingsMutation } from './settings-forms';

export function ModelSettings({ provider, revision, writable, mutate }: { provider: ProviderStatus; revision: string; writable: boolean; mutate: SettingsMutation }) {
  const [editing, setEditing] = useState<CatalogModel | 'new'>();
  const [discovered, setDiscovered] = useState<DiscoveredModel[]>([]);
  const [candidate, setCandidate] = useState<DiscoveredModel>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function discover() {
    setBusy(true); setMessage('');
    try { const result = parseDiscovery(await mutate(`${providerPath(provider.id)}/models/discover`, {}, false)); setDiscovered(result.models); setMessage(result.message ?? `Found ${result.models.length} models. Select a model to review and add it.`); }
    catch (cause) { setMessage(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <section className="provider-models" aria-label={`${provider.id} models`}>
    <h4>Models</h4>
    {provider.models?.map((model) => <div className="provider-model" key={model.ref}>
      <strong>{model.ref}</strong><span>{model.protocols.join(', ')}</span>
      <span>{model.pricing === 'subscription' ? 'Subscription · token cost unknown' : model.price_per_mtok ? `$${model.price_per_mtok.in ?? '?'} input / $${model.price_per_mtok.out ?? '?'} output per million tokens` : 'Pricing unknown'}</span>
      {model.source && <span className="provider-source">Source: {model.source}</span>}
      {writable && <button type="button" onClick={() => { setCandidate(undefined); setEditing(model); }}>Edit model {model.ref}</button>}
    </div>)}
    {!provider.models?.length && <p>No catalog models.</p>}
    {writable && <div className="provider-actions"><button type="button" disabled={busy} onClick={() => void discover()}>{busy ? 'Discovering…' : 'Discover models'}</button><button type="button" onClick={() => { setCandidate(undefined); setEditing('new'); }}>Add model manually</button></div>}
    {message && <p role="status">{message}</p>}
    {discovered.length > 0 && <ul className="provider-discovered">{discovered.map((model) => <li key={model.id}><span>{model.id}</span><button type="button" onClick={() => { setCandidate(model); setEditing('new'); }}>Review {model.id}</button></li>)}</ul>}
    {writable && editing && <ModelForm key={editing === 'new' ? candidate?.id ?? 'new' : editing.ref} provider={provider} model={editing === 'new' ? undefined : editing} candidate={candidate} revision={revision} mutate={mutate} onClose={() => setEditing(undefined)} />}
  </section>;
}
function ModelForm({ provider, model, candidate, revision, mutate, onClose }: { provider: ProviderStatus; model?: CatalogModel; candidate?: DiscoveredModel; revision: string; mutate: SettingsMutation; onClose: () => void }) {
  const [baseRevision] = useState(revision);
  const [ref, setRef] = useState(model?.ref ?? (candidate ? `${provider.id}/${candidate.id}` : `${provider.id}/`));
  const subscription = provider.kind === 'subscription';
  const allowed: Protocol[] = subscription
    ? (provider.vendor === 'chatgpt' ? ['responses'] : ['anthropic'])
    : protocols.filter((p) => !!provider.config?.apis?.[p]);
  const [selectedProtocols, setProtocols] = useState<Protocol[]>(() => {
    const chosen = (model?.protocols ?? allowed).filter((p) => allowed.includes(p));
    return chosen.length ? chosen : allowed;
  });
  const [input, setInput] = useState(String(model?.price_per_mtok?.in ?? ''));
  const [output, setOutput] = useState(String(model?.price_per_mtok?.out ?? ''));
  const [cacheRead, setCacheRead] = useState(String(model?.price_per_mtok?.cache_read ?? ''));
  const [cacheWrite, setCacheWrite] = useState(String(model?.price_per_mtok?.cache_write ?? ''));
  const [source, setSource] = useState(model?.source ?? (candidate?.source.startsWith('https://') ? candidate.source : ''));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function save() {
    setError('');
    if (!ref.startsWith(`${provider.id}/`) || ref === `${provider.id}/` || /\s/.test(ref)) { setError('Enter a model ref starting with the provider ID followed by / and a model ID.'); return; }
    if (!selectedProtocols.length || selectedProtocols.some((p) => !allowed.includes(p))) { setError('Select a protocol this provider supports.'); return; }
    if (!subscription) {
      try { const url = new URL(source); if (url.protocol !== 'https:' || url.username || url.password) throw new Error(); }
      catch { setError('API pricing requires an HTTPS source URL.'); return; }
      if (!input.trim() || !output.trim() || [input, output, cacheRead, cacheWrite].filter((v) => v.trim()).some((v) => !Number.isFinite(Number(v)) || Number(v) < 0)) {
        setError('Enter input and output prices from the source, as finite nonnegative numbers.'); return;
      }
    }
    const price: Record<string, number> = { ...model?.price_per_mtok, in: Number(input), out: Number(output) };
    if (cacheRead.trim()) price.cache_read = Number(cacheRead); else delete price.cache_read;
    if (cacheWrite.trim()) price.cache_write = Number(cacheWrite); else delete price.cache_write;
    const saved: CatalogModel = { ...model, ref: ref.trim(), protocols: selectedProtocols };
    delete saved.price_per_mtok; delete saved.pricing; delete saved.source;
    if (subscription) { saved.pricing = 'subscription'; if (source.trim()) saved.source = source.trim(); }
    else { saved.price_per_mtok = price; saved.source = source.trim(); }
    setBusy(true);
    try { await mutate(`${providerPath(provider.id)}/models`, { revision: baseRevision, model: saved }); onClose(); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <form className="provider-form" aria-label={model ? `Edit model ${model.ref}` : 'Add model'} onSubmit={(e) => { e.preventDefault(); void save(); }}>
    <h4>{model ? 'Edit catalog model' : 'Add catalog model'}</h4>
    {error && <p className="providers-error" role="alert">{error}</p>}
    <fieldset disabled={busy} className="provider-form-fields">
      <label>Model ref<input required value={ref} disabled={!!model} onChange={(e) => setRef(e.target.value)} /></label>
      <ProtocolFields value={selectedProtocols} onChange={setProtocols} options={allowed} />
      {!allowed.length && <p>Configure a provider endpoint before adding a model.</p>}
      {subscription ? <p>Subscription pricing. Token cost remains unknown.</p> : <><p>Copy the vendor’s published USD prices per million tokens. Discovery does not supply prices.</p><label>Input price per million tokens<input type="number" required min="0" step="any" value={input} onChange={(e) => setInput(e.target.value)} /></label><label>Output price per million tokens<input type="number" required min="0" step="any" value={output} onChange={(e) => setOutput(e.target.value)} /></label><label>Cache read price (optional)<input type="number" min="0" step="any" value={cacheRead} onChange={(e) => setCacheRead(e.target.value)} /></label><label>Cache write price (optional)<input type="number" min="0" step="any" value={cacheWrite} onChange={(e) => setCacheWrite(e.target.value)} /></label></>}
      <label>Pricing source URL{subscription ? ' (optional)' : ''}<input type="url" required={!subscription} placeholder="https://…" value={source} onChange={(e) => setSource(e.target.value)} /></label>
      <div className="provider-actions"><button type="submit" disabled={busy || !allowed.length}>{busy ? 'Saving…' : 'Save model'}</button><button type="button" onClick={onClose}>Cancel model</button></div>
    </fieldset>
  </form>;
}

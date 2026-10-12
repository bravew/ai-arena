import { useState } from 'react';
import type { CredentialStatus, ProviderStatus } from './http-source';
import { errorMessage, providerPath, type SettingsMutation } from './settings-forms';
export function KeySettings({ provider, mutate }: { provider: ProviderStatus; mutate: SettingsMutation }) {
  const credentials = provider.credentials ?? provider.config?.keys?.map((key) => ({ ...key, configured: false, source: null })) ?? [];
  return <section className="provider-keys" aria-label={`${provider.id} keys`}><h4>API keys</h4><p>Key values are write-only. Arena saves its own local copy; environment variables take precedence.</p>{credentials.map((key) => <KeyForm key={key.id} providerId={provider.id} credential={key} mutate={mutate} />)}</section>;
}
function KeyForm({ providerId, credential, mutate }: { providerId: string; credential: CredentialStatus; mutate: SettingsMutation }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function submit(forget: boolean) {
    setBusy(true); setMessage('');
    const secret = value; setValue('');
    try { await mutate(`${providerPath(providerId)}/keys/${encodeURIComponent(credential.id)}/${forget ? 'forget' : 'save'}`, forget ? {} : { value: secret }); setMessage(forget ? 'Arena key copy forgotten.' : 'Key saved.'); }
    catch (cause) { setMessage(errorMessage(cause)); } finally { setBusy(false); }
  }
  return <form className="provider-key-form" onSubmit={(e) => { e.preventDefault(); void submit(false); }}>
    <p><strong>{credential.id}</strong> · {credential.env} · {credential.configured ? 'Configured' : 'Missing'}{credential.source ? ` · ${credential.source}` : ''}</p>
    <label>Key value for {credential.id}<input type="password" autoComplete="new-password" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} disabled={busy} /></label>
    <div className="provider-actions"><button type="submit" disabled={busy || !value.trim()}>Save key</button><button type="button" disabled={busy} onClick={() => void submit(true)}>Forget Arena key copy</button></div>
    {message && <p role="status">{message}</p>}
  </form>;
}

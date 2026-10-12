import { useEffect, useState } from 'react';
import { fetchLogin, parseLogin, safeMessage, type LoginStatus, type ProviderStatus } from './http-source';
import { errorMessage, type SettingsMutation } from './settings-forms';
const terminal = (state: string) => ['succeeded', 'completed', 'failed', 'cancelled', 'expired'].includes(state);
function signInUrl(value?: string): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); if (url.protocol === 'https:' && !url.username && !url.password) return url.href; } catch { /* Invalid URLs are not linked. */ }
  return undefined;
}
export function AccountSettings({ provider, mutate, refresh }: { provider: ProviderStatus; mutate: SettingsMutation; refresh: () => Promise<void> }) {
  const [login, setLogin] = useState<LoginStatus>();
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const vendor = provider.vendor;
  useEffect(() => {
    if (!login || terminal(login.state)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await fetchLogin(login!.id);
        if (!active) return;
        setLogin(next);
        if (terminal(next.state)) { await refresh(); return; }
        timer = setTimeout(() => void poll(), 1500);
      } catch (cause) { if (active) setError(errorMessage(cause)); }
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [login, refresh]);
  if (vendor !== 'claude' && vendor !== 'chatgpt') return null;
  const path = `/api/accounts/${vendor}`;
  async function action(kind: 'connect' | 'keychain' | 'forget' | 'login' | 'code' | 'cancel') {
    setBusy(true); setError('');
    const submittedCode = code; setCode('');
    try {
      if (kind === 'login') setLogin(parseLogin(await mutate(`${path}/login`, {}, false)));
      else if (kind === 'code' || kind === 'cancel') {
        if (!login) return;
        const result = await mutate(`/api/account-logins/${encodeURIComponent(login.id)}/${kind}`, kind === 'code' ? { code: submittedCode } : {}, false);
        setLogin(parseLogin(result));
        if (terminal(String(result.state))) await refresh();
      } else {
        const result = await mutate(`${path}/${kind === 'forget' ? 'forget' : 'import'}`, kind === 'forget' ? {} : { keychain: kind === 'keychain' });
        setError(typeof result.message === 'string' ? safeMessage(result.message) : 'Arena account copy updated.');
        if (kind === 'forget') setLogin(undefined);
      }
    } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  }
  const activeLogin = !!login && !terminal(login.state);
  const url = signInUrl(login?.url);
  const ready = provider.sign_in === 'cli_discovered' || provider.sign_in === 'stored';
  const name = vendor === 'claude' ? 'Claude' : 'ChatGPT';
  const controls = <>
    <div className="provider-actions"><button disabled={busy || activeLogin} onClick={() => void action('connect')}>Connect existing</button><button disabled={busy || activeLogin} onClick={() => void action('keychain')}>Import macOS Keychain</button><button disabled={busy || activeLogin} onClick={() => void action('login')}>Sign in</button><button disabled={busy || activeLogin} onClick={() => void action('forget')}>Disconnect Arena copy</button></div>
    {error && <p role="status">{error}</p>}
    {login && <div className="provider-login"><p role="status">Sign-in: {login.state.replaceAll('_', ' ')}{login.message ? ` · ${safeMessage(login.message)}` : ''}</p>
      {url && activeLogin && <a href={url} target="_blank" rel="noopener noreferrer">Open vendor sign-in</a>}
      {login.user_code && activeLogin && <p>Device code: <code>{login.user_code}</code></p>}
      {['awaiting_code', 'code_required'].includes(login.state) && <form onSubmit={(e) => { e.preventDefault(); void action('code'); }}><label>Sign-in code<input autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} required /></label><button disabled={busy} type="submit">Submit code</button></form>}
      {activeLogin && <button disabled={busy} onClick={() => void action('cancel')}>Cancel sign-in</button>}
    </div>}
  </>;
  return <section aria-label={`${provider.id} subscription`} className="provider-account">
    <h4>Subscription account</h4>
    {ready
      ? <>
        <p>{provider.sign_in === 'stored' ? `Using the ${name} sign-in saved in Arena.` : `Using the ${name} sign-in already on this computer.`} No OAuth step or API key is needed.</p>
        <details open={activeLogin || !!error}><summary>Use a different account</summary>{controls}</details>
      </>
      : <>
        <p>No {name} sign-in was found on this computer. Connect an existing sign-in or sign in through the vendor. Disconnect removes Arena’s saved copy.</p>
        {controls}
      </>}
  </section>;
}

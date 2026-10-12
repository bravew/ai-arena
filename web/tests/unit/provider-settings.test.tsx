import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProviderForm, type SettingsMutation } from '../../src/views/providers/settings-forms';
import { fetchProviders, mutateSettings, ProviderApiError } from '../../src/views/providers/http-source';
import { KeySettings } from '../../src/views/providers/KeySettings';
import { ProvidersView } from '../../src/views/providers/ProvidersView';
const provider = { id: 'api', kind: 'api', enabled: true, runtime: 'gateway', vendor: null, cli_found: null, version: null, auth: null, status: 'configured', hint: null, key_envs: ['API_KEY'], config: { id: 'api', apis: { chat: 'https://example.test/v1' }, keys: [{ id: 'main', env: 'API_KEY', weight: 2 }], limits: { concurrency: 4, rpm: 9 }, headers: { 'x-custom': 'keep' } } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.getElementById('arena-static-data')?.remove(); });
describe('provider settings', () => {
  it('preserves hidden extras and pins the revision from when the editor opened', async () => {
    const mutate = vi.fn(async () => ({}));
    const view = render(<ProviderForm config={provider.config} revision="r1" mutate={mutate} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText('Concurrency'), { target: { value: '6' } });
    view.rerender(<ProviderForm config={provider.config} revision="r2" mutate={mutate} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save provider' }));
    await waitFor(() => expect(mutate).toHaveBeenCalled());
    expect(mutate.mock.calls[0]).toEqual(['/api/providers/api/config', { revision: 'r1', provider: { ...provider.config, enabled: true, subscription: null, limits: { concurrency: 6, rpm: 9 } } }]);
  });
  it('clears key values immediately even when saving fails', async () => {
    const mutate = vi.fn(async () => { throw new Error('save failed'); });
    render(<KeySettings provider={provider} mutate={mutate} />);
    fireEvent.change(screen.getByLabelText('Key value for main'), { target: { value: 'fake-only-value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    expect(screen.getByLabelText('Key value for main')).toHaveProperty('value', '');
    expect(await screen.findByText('save failed')).toBeTruthy();
    expect(mutate).toHaveBeenCalledWith('/api/providers/api/keys/main/save', { value: 'fake-only-value' });
  });
  it('rejects malformed provider responses and does not treat them as empty', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ providers: [{ id: 'x' }], instructions: {} }), { status: 200 }));
    await expect(fetchProviders(fetcher)).rejects.toThrow('invalid shape');
  });
  it('sends CSRF with mutations and turns conflict errors into recovery advice', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ error: 'revision conflict' }), { status: 409 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(mutateSettings('/api/providers', { revision: 'r1' }, 'csrf')).rejects.toBeInstanceOf(ProviderApiError);
    expect(fetcher).toHaveBeenCalledWith('/api/providers', expect.objectContaining({ redirect: 'error', headers: { 'Content-Type': 'application/json', 'X-Arena-CSRF': 'csrf' } }));
    await expect(mutateSettings('/api/providers', {}, 'csrf')).rejects.toThrow('Refresh providers');
  });
  it('rejects an insecure endpoint and creates a subscription with the local CLI runtime', async () => {
    const mutate = vi.fn<SettingsMutation>(async () => ({}));
    render(<ProviderForm revision="r1" mutate={mutate} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText('Provider ID'), { target: { value: 'bad' } });
    fireEvent.change(screen.getByLabelText('chat endpoint'), { target: { value: 'http://provider.example/v1' } });
    fireEvent.change(screen.getByLabelText('Environment variable 1'), { target: { value: 'BAD_KEY' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save provider' }));
    expect(await screen.findByText(/chat endpoint: Use an absolute HTTPS endpoint/)).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
    cleanup();
    render(<ProviderForm revision="r1" mutate={mutate} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText('Provider ID'), { target: { value: 'plan' } });
    fireEvent.click(screen.getByRole('radio', { name: 'subscription' }));
    fireEvent.click(screen.getByRole('radio', { name: 'chatgpt' }));
    fireEvent.change(screen.getByLabelText('Plan'), { target: { value: 'plus' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save provider' }));
    await waitFor(() => expect(mutate).toHaveBeenCalled());
    expect(mutate.mock.calls[0]?.[1]).toMatchObject({ provider: { id: 'plan', keys: [], apis: {}, subscription: { vendor: 'chatgpt', plan: 'plus', runtime: 'local-cli' } } });
  });
  it('never loads live provider data in a static export', async () => {
    const marker = document.createElement('script'); marker.id = 'arena-static-data'; document.body.append(marker);
    const load = vi.fn(); render(<ProvidersView load={load} />);
    expect(await screen.findByText(/read-only in this static export/)).toBeTruthy();
    expect(load).not.toHaveBeenCalled();
  });
});

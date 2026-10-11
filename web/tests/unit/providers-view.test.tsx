import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProvidersView } from '../../src/views/providers/ProvidersView';
import type { ProvidersSnapshot } from '../../src/views/providers/http-source';

const snapshot: ProvidersSnapshot = {
  instructions: {
    subscription: 'arena providers add claude|codex',
    api: 'arena providers add <provider> --env ENV_NAME',
    note: 'Name the environment variable only; never paste a key in the viewer.',
  },
  providers: [
    { id: 'claude-plan', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'max', cli_found: true, binary: '/usr/bin/claude', version: '2.1.296', auth: 'authenticated', sign_in: 'stored', status: 'ok', detail: 'ready', hint: null, key_envs: [] },
    { id: 'codex-plan', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'chatgpt', plan: 'plus', cli_found: true, binary: '', version: '0.162.1', auth: 'unauthenticated', sign_in: 'missing', status: 'auth_required', detail: 'not signed in', hint: 'codex login', key_envs: [] },
    { id: 'missing-cli', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'pro', cli_found: false, binary: '', version: null, auth: 'unknown', sign_in: 'missing', status: 'not_installed', detail: 'CLI not found', hint: 'install claude', key_envs: [] },
    { id: 'old-cli', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'pro', cli_found: true, binary: '', version: '1.0.0', auth: 'authenticated', sign_in: 'stored', status: 'incompatible_version', detail: 'update required', hint: 'upgrade claude', key_envs: [] },
    { id: 'model-missing', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'chatgpt', plan: 'plus', cli_found: true, binary: '', version: '0.162.1', auth: 'authenticated', sign_in: 'stored', status: 'unsupported_model', detail: 'model missing', hint: 'run arena providers models', key_envs: [] },
    { id: 'broken-probe', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'pro', cli_found: true, binary: '', version: '2.1.296', auth: 'unknown', sign_in: 'stored', status: 'protocol_failure', detail: 'invalid response', hint: null, key_envs: [] },
    { id: 'forbidden', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'pro', cli_found: true, binary: '', version: '2.1.296', auth: 'unknown', sign_in: 'stored', status: 'permission_failure', detail: 'permission denied', hint: null, key_envs: [] },
    { id: 'anthropic', kind: 'api', enabled: true, runtime: 'gateway', vendor: null, cli_found: null, version: null, auth: null, status: 'configured', detail: '', hint: null, key_envs: ['ANTHROPIC_API_KEY'] },
    { id: 'disabled', kind: 'api', enabled: false, runtime: 'gateway', vendor: null, cli_found: null, version: null, auth: null, status: 'disabled', detail: '', hint: null, key_envs: ['OTHER_API_KEY'] },
  ],
};

const load = async () => snapshot;

afterEach(() => cleanup());

describe('ProvidersView', () => {
  it('shows provider runtime, auth, recovery, env names and status variants', async () => {
    render(<ProvidersView load={load} />);
    expect(await screen.findByRole('heading', { name: 'Providers' })).toBeTruthy();
    expect(screen.getByText('Ready')).toBeTruthy();
    expect(screen.getAllByText('Sign-in', { selector: 'dt' })).toHaveLength(7);
    expect(screen.getByText('Sign-in required')).toBeTruthy();
    expect(screen.getByText('CLI not installed')).toBeTruthy();
    expect(screen.getByText('Update required')).toBeTruthy();
    expect(screen.getByText('Model unavailable')).toBeTruthy();
    expect(screen.getByText('Could not read CLI status')).toBeTruthy();
    expect(screen.getByText('Permission denied')).toBeTruthy();
    expect(screen.getByText('Configured')).toBeTruthy();
    expect(screen.getByText('Disabled')).toBeTruthy();
    expect(screen.getByText('Environment variable name: ANTHROPIC_API_KEY')).toBeTruthy();
    expect(screen.getByText('codex login')).toBeTruthy();
    expect(screen.queryByText('/usr/bin/claude')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Test connection' })).toHaveLength(8);
  });

  it('states that a connection test sends one capped request and displays the result', async () => {
    const runTest = vi.fn(async () => ({ ok: true, message: 'One capped model call was sent through the vendor CLI and recorded.' }));
    render(<ProvidersView load={load} runTest={runTest} />);
    fireEvent.click(await screen.findAllByRole('button', { name: 'Test connection' }).then((buttons) => buttons[0]!));
    expect(screen.getAllByText('This sends one capped model request.')).toHaveLength(8);
    expect(await screen.findByText('One capped model call was sent through the vendor CLI and recorded.')).toBeTruthy();
    expect(runTest).toHaveBeenCalledWith('claude-plan');
  });

  it('shows an API error without showing a secret value', async () => {
    const failed = async () => { throw new Error('connection test is rate limited; wait 30 seconds'); };
    render(<ProvidersView load={load} runTest={failed} />);
    await screen.findAllByRole('heading', { name: 'claude-plan' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Test connection' })[0]!);
    expect(await screen.findByText('connection test is rate limited; wait 30 seconds')).toBeTruthy();
    expect(screen.queryByText(/sk-[a-z0-9]+/i)).toBeNull();
  });
});

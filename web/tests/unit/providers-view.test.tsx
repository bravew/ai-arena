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
    const runTest = vi.fn(async () => ({ ok: true, message: 'One capped model call was sent through the vendor CLI and recorded.', source: 'cli_reported', tokens: { input: 2979, output: 5, cache_read: 11008 } }));
    render(<ProvidersView load={load} runTest={runTest} />);
    fireEvent.click(await screen.findAllByRole('button', { name: 'Test connection' }).then((buttons) => buttons[0]!));
    expect(screen.getAllByText(/This sends one (capped model|real) request/).length).toBe(8);
    expect(screen.getAllByText(/uses subscription quota/).length).toBeGreaterThan(0);
    expect(await screen.findByText('One capped model call was sent through the vendor CLI and recorded.')).toBeTruthy();
    expect(screen.getByText('Test passed')).toBeTruthy();
    expect(screen.getByText(/2,979 input · 5 output · 11,008 cache read/)).toBeTruthy();
    expect(runTest).toHaveBeenCalledWith('claude-plan');
  });

  it('shows an API error without showing a secret value', async () => {
    const failed = async () => { throw new Error('connection test is rate limited; wait 30 seconds'); };
    render(<ProvidersView load={load} runTest={failed} />);
    await screen.findAllByRole('heading', { name: 'claude-plan' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Test connection' })[0]!);
    expect(await screen.findByText('connection test is rate limited; wait 30 seconds')).toBeTruthy();
    expect(screen.queryByText('Test passed')).toBeNull();
    expect(screen.queryByText(/sk-[a-z0-9]+/i)).toBeNull();
  });

  it('marks a failed test as failed and shows no usage line when none was reported', async () => {
    const runTest = vi.fn(async () => ({ ok: false, status: 'failed', message: 'The CLI reported an expired sign-in.' }));
    render(<ProvidersView load={load} runTest={runTest} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Test connection' }))[0]!);
    expect(await screen.findByText('Test failed')).toBeTruthy();
    expect(screen.queryByText(/Usage reported by/)).toBeNull();
  });
});

describe('RunLauncher', () => {
  const writable: ProvidersSnapshot = { ...snapshot, revision: 'r', catalog_revision: 'c', settings_home: '/tmp/home', capabilities: { settings: true, launch: true } };
  const options = {
    suites: [{ id: 'tiny', tasks: 2, kinds: ['codegen'] }],
    sets: [{ name: 'sets/pair', contestants: [{ label: 'a', model: 'anthropic-max/claude-sonnet-5-5', runnable: true, kind: 'subscription', reason: '' }] }],
    models: [{ ref: 'anthropic-max/claude-sonnet-5-5', provider: 'anthropic-max', vendor: 'claude', enabled: true }],
    docker_available: false, limits: { max_repeats: 10, max_trials: 200 }, active_job: null,
  };
  const plan = { suite: 'tiny', set: 'sets/pair', repeats: 2, tasks: 2, contestants: 1, trials: 4, subscription_trials: 4, scorers: ['execution'], startable: true, blockers: [] };
  const job = { id: '0123456789abcdef', suite: 'tiny', set: 'sets/pair', repeats: 2, trials: 4, score_execution: false, state: 'running', run_id: 'abc', started_at: 1, finished_at: null, exit_code: null, message: '' };
  function setup(extra: Partial<Record<string, unknown>> = {}) {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => new Response(JSON.stringify(path === '/api/provider-settings' ? { csrf_token: 'tok' } : {}), { status: 200 })));
    const api = {
      options: vi.fn(async () => options), jobs: vi.fn(async () => []), job: vi.fn(async () => ({ ...job, log_tail: 'run_id=abc' })),
      saveSet: vi.fn(async () => ({ ok: true })), plan: vi.fn(async () => plan), start: vi.fn(async () => job),
      cancel: vi.fn(async () => job), resume: vi.fn(async () => job), ...extra,
    };
    render(<ProvidersView load={async () => writable} launch={api as never} />);
    return api;
  }
  afterEach(() => vi.unstubAllGlobals());

  it('previews the plan, requires quota confirmation, then starts', async () => {
    const api = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview plan' }));
    expect(await screen.findByText('2 tasks × 1 contestant × 2 repeats = 4 trials')).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start run' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/uses my subscription quota/));
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    await vi.waitFor(() => expect(api.start).toHaveBeenCalledWith('tiny', 'sets/pair', 1, false, 'tok'));
  });

  it('saves a contestant set from chosen models and a valid name', async () => {
    const api = setup();
    const save = (await screen.findByRole('button', { name: 'Save contestant set' })) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('anthropic-max/claude-sonnet-5-5'));
    fireEvent.change(screen.getByLabelText('Set name'), { target: { value: 'pair' } });
    fireEvent.click(save);
    await vi.waitFor(() => expect(api.saveSet).toHaveBeenCalledWith('pair', ['anthropic-max/claude-sonnet-5-5'], false, 'tok'));
  });

  it('disables execution scoring without Docker and blocks a plan with blockers', async () => {
    setup({ plan: vi.fn(async () => ({ ...plan, startable: false, blockers: ['run them in the CLI'] })) });
    expect(((await screen.findByLabelText(/Run execution scorers in Docker/)) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Preview plan' }));
    expect(await screen.findByText('run them in the CLI')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Start run' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows a running job with cancel and its log', async () => {
    const api = setup({ jobs: vi.fn(async () => [job]) });
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel run' }));
    await vi.waitFor(() => expect(api.cancel).toHaveBeenCalledWith(job.id, 'tok'));
    fireEvent.click(screen.getByRole('button', { name: 'Show log' }));
    expect((await screen.findByLabelText('Run log')).textContent).toBe('run_id=abc');
    expect((screen.getByRole('button', { name: 'Start run' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

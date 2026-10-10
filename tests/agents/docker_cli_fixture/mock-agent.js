const http = require('http');

if (process.argv[2] === '--version') {
  process.stdout.write('mock-agent 1.2.3\n');
  process.exit(0);
}
if (process.argv[2] !== 'run') process.exit(2);

const gateway = process.env.ARENA_GATEWAY_URL;
const token = process.env.ARENA_GATEWAY_TOKEN;
const protocol = process.env.MOCK_PROTOCOL || 'anthropic';
const url = new URL(`/${protocol}`, gateway);
const req = http.request(url, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
}, (res) => {
  res.resume();
  res.on('end', () => process.exit(res.statusCode === 200 ? 0 : 1));
});
req.on('error', () => process.exit(1));
req.end(JSON.stringify({ model: 'pinned-mock', messages: [{ role: 'user', content: 'ping' }] }));

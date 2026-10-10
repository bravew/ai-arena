const fs = require('fs');
const http = require('http');

http.createServer((req, res) => {
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', () => {
    const token = (req.headers.authorization || '').replace(/^Bearer /, '');
    fs.writeFileSync('/results/first-call.json', JSON.stringify({
      token,
      protocol: req.url.slice(1),
    }));
    const body = JSON.stringify({ id: 'mock', choices: [{ message: { content: 'ok' } }] });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    res.end(body);
  });
}).listen(7400, '0.0.0.0');

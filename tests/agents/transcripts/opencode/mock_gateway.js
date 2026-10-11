const crypto = require('crypto');
const fs = require('fs');
const http = require('http');

const resultPath = process.env.RESULT_PATH || '/results/requests.jsonl';
http.createServer((req, res) => {
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', () => {
    const request = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const messages = Array.isArray(request.messages) ? request.messages : [];
    const system = messages.filter(message => message.role === 'system').map(message => {
      const content = typeof message.content === 'string' ? message.content : JSON.stringify(message.content);
      return { bytes: Buffer.byteLength(content), sha256: crypto.createHash('sha256').update(content).digest('hex') };
    });
    const record = {
      token: (req.headers.authorization || '').replace(/^Bearer /, ''),
      path: req.url,
      model: request.model,
      system,
      prompt_parts: { system_count: system.length, system_bytes: system.reduce((sum, item) => sum + item.bytes, 0) },
    };
    fs.appendFileSync(resultPath, JSON.stringify(record) + '\n');
    const body = JSON.stringify({
      id: 'chatcmpl-mock',
      object: 'chat.completion',
      choices: [{ index: 0, message: { role: 'assistant', content: 'Mock provider completed the task.' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 31, completion_tokens: 7, total_tokens: 38 },
    });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    res.end(body);
  });
}).listen(7400, '0.0.0.0');

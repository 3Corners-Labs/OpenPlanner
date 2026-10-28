/**
 * Test host website for the <gov-booking> widget (ADR 0035, EM-4): a page on another origin
 * (http://localhost:8001) that loads the widget script from the app under test (BASE_URL).
 * Its CSP allows only what docs/widget.md tells hosts to allow, so the specs prove that the
 * widget needs nothing more. No dependencies: Node's own http server.
 */
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';

const port = Number(process.env.WIDGET_HOST_PORT ?? 8001);
const app = new URL(process.env.BASE_URL ?? 'http://127.0.0.1:8000').origin;
const files = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/host.js': ['host.js', 'text/javascript; charset=utf-8'],
  '/host.css': ['host.css', 'text/css; charset=utf-8'],
};
const csp = [
  "default-src 'none'",
  `script-src 'self' ${app}`,
  `connect-src ${app}`,
  "style-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

createServer((request, response) => {
  const path = new URL(request.url ?? '/', `http://localhost:${port}`).pathname;
  const file = files[path];
  if (!file) {
    response.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
    return;
  }
  const body = readFileSync(new URL(file[0], import.meta.url), 'utf8').replaceAll('{{APP}}', app);
  response.writeHead(200, { 'content-type': file[1], 'content-security-policy': csp }).end(body);
}).listen(port, () => console.log(`widget host page on http://localhost:${port} (app ${app})`));

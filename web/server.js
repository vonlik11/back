const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = Number(process.env.PORT || 3000);
const PUBLIC_DIR = __dirname;
const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(__dirname, '.mark-ai-data'));
const DATA_FILE = path.join(DATA_DIR, 'companies.json');
const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'petmatch-2d396';
const FIREBASE_CERTS_URL = 'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com';
const MAX_BODY = 1024 * 1024;
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml'
};

let certCache = { expires: 0, certs: {} };

function sendJson(res, status, value) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(value));
}

function readStore() {
  try {
    const value = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch (error) {
    if (error.code !== 'ENOENT') console.error('Could not read company store:', error.message);
    return {};
  }
}

function writeStore(store) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const tempPath = `${DATA_FILE}.${process.pid}.tmp`;
  fs.writeFileSync(tempPath, JSON.stringify(store, null, 2), { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tempPath, DATA_FILE);
}

function fetchFirebaseCerts() {
  if (certCache.expires > Date.now() && Object.keys(certCache.certs).length) return Promise.resolve(certCache.certs);
  return new Promise((resolve, reject) => {
    const request = https.get(FIREBASE_CERTS_URL, { timeout: 8000 }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => {
        if (response.statusCode !== 200) return reject(new Error(`Firebase cert endpoint returned ${response.statusCode}`));
        try {
          const certs = JSON.parse(body);
          const maxAge = Number((response.headers['cache-control'] || '').match(/max-age=(\d+)/)?.[1] || 3600);
          certCache = { certs, expires: Date.now() + Math.max(60, maxAge - 60) * 1000 };
          resolve(certs);
        } catch (error) { reject(error); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('Firebase certificate request timed out')));
    request.on('error', reject);
  });
}

function decodePart(part) {
  return JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
}

async function verifyFirebaseToken(token) {
  if (!token || token.length > 8192) throw new Error('Missing or invalid bearer token');
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('Malformed bearer token');
  const header = decodePart(parts[0]);
  const claims = decodePart(parts[1]);
  if (header.alg !== 'RS256' || !header.kid) throw new Error('Unsupported token signature');
  const certs = await fetchFirebaseCerts();
  const cert = certs[header.kid];
  if (!cert) { certCache.expires = 0; throw new Error('Unknown token signing key'); }
  const valid = crypto.createVerify('RSA-SHA256').update(`${parts[0]}.${parts[1]}`).verify(cert, Buffer.from(parts[2], 'base64url'));
  const now = Math.floor(Date.now() / 1000);
  if (!valid || claims.iss !== `https://securetoken.google.com/${FIREBASE_PROJECT_ID}` || claims.aud !== FIREBASE_PROJECT_ID ||
      typeof claims.sub !== 'string' || !claims.sub || claims.sub.length > 128 || !Number.isFinite(claims.exp) ||
      !Number.isFinite(claims.iat) || !Number.isFinite(claims.auth_time) || claims.exp <= now ||
      claims.iat > now + 60 || claims.auth_time > now + 60) throw new Error('Invalid Firebase ID token');
  return claims.sub;
}

async function requireUser(req) {
  const match = /^Bearer\s+(.+)$/i.exec(req.headers.authorization || '');
  if (!match) throw new Error('Authorization required');
  return verifyFirebaseToken(match[1]);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.setEncoding('utf8');
    req.on('data', chunk => {
      body += chunk;
      if (Buffer.byteLength(body, 'utf8') > MAX_BODY) {
        reject(Object.assign(new Error('Request body too large'), { statusCode: 413 }));
        req.destroy();
      }
    });
    req.on('end', () => {
      try { resolve(JSON.parse(body || '{}')); }
      catch (_) { reject(Object.assign(new Error('Invalid JSON'), { statusCode: 400 })); }
    });
    req.on('error', reject);
  });
}

function cleanCompany(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid company');
  const text = (key, max) => typeof value[key] === 'string' ? value[key].trim().slice(0, max) : '';
  const id = text('id', 64), name = text('name', 100);
  if (!/^[a-zA-Z0-9_-]+$/.test(id) || !name) throw new Error('Company id and name are required');
  return {
    id, name, full: text('full', 140) || name, industry: text('industry', 120),
    initials: text('initials', 4), site: text('site', 300),
    platforms: Array.isArray(value.platforms) ? [...new Set(value.platforms.filter(x => ['tg', 'vk', 'max'].includes(x)))] : []
  };
}

async function handleApi(req, res, pathname) {
  let uid;
  try { uid = await requireUser(req); }
  catch (error) { return sendJson(res, 401, { error: 'Authentication required' }); }

  if (req.method === 'GET' && pathname === '/api/companies') {
    const record = readStore()[uid] || { companies: [], activeCompanyId: null };
    return sendJson(res, 200, record);
  }
  if (req.method === 'PUT' && pathname === '/api/companies') {
    try {
      const body = await readJson(req);
      if (!Array.isArray(body.companies) || body.companies.length > 100) return sendJson(res, 400, { error: 'Companies must be an array of at most 100 items' });
      const companies = body.companies.map(cleanCompany);
      if (new Set(companies.map(company => company.id)).size !== companies.length) return sendJson(res, 400, { error: 'Company ids must be unique' });
      const activeCompanyId = companies.some(company => company.id === body.activeCompanyId) ? body.activeCompanyId : (companies[0]?.id || null);
      const store = readStore();
      store[uid] = { companies, activeCompanyId, updatedAt: new Date().toISOString() };
      writeStore(store);
      return sendJson(res, 200, { ok: true, activeCompanyId, updatedAt: store[uid].updatedAt });
    } catch (error) {
      return sendJson(res, error.statusCode || 400, { error: error.message || 'Could not save companies' });
    }
  }
  return sendJson(res, 404, { error: 'Not found' });
}

const server = http.createServer(async (req, res) => {
  let pathname;
  try { pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname); }
  catch (_) { return sendJson(res, 400, { error: 'Invalid URL' }); }

  if (pathname.startsWith('/api/')) return handleApi(req, res, pathname);
  if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { error: 'Method not allowed' });
  const relativePath = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const filePath = path.resolve(PUBLIC_DIR, relativePath);
  if (filePath === DATA_DIR || filePath.startsWith(DATA_DIR + path.sep)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('404 Not Found');
  }
  if (filePath !== PUBLIC_DIR && !filePath.startsWith(PUBLIC_DIR + path.sep)) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('403 Forbidden');
  }
  fs.stat(filePath, (error, stats) => {
    if (error || !stats.isFile()) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('404 Not Found');
    }
    res.writeHead(200, { 'Content-Type': MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' });
    if (req.method === 'HEAD') return res.end();
    fs.createReadStream(filePath).pipe(res);
  });
});

server.listen(PORT, () => console.log(`AI-Marketer Mini App and API running on port ${PORT}`));

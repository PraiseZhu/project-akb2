'use strict';

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const readline = require('node:readline');

const DEFAULT_HOST = '172.26.163.197';
const DEFAULT_PORT = 8788;
const DEFAULT_LIMIT = 0;
const REQUEST_TIMEOUT_MS = 8000;
const SNAPSHOT_PATH =
  process.env.AKB2_PLUGIN_SNAPSHOT || path.join(__dirname, '..', 'snapshot.json');

function reply(message) {
  process.stdout.write(JSON.stringify(message) + '\n');
}

function secretToken(request) {
  const secrets = request && request.cindy && request.cindy.secrets;
  if (!secrets || typeof secrets.retrieval_token !== 'string') return '';
  return secrets.retrieval_token;
}

function capLimit(raw) {
  if (raw === undefined || raw === null || raw === '') return DEFAULT_LIMIT;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_LIMIT;
  return Math.floor(n);
}

function loadSnapshot() {
  try {
    const parsed = JSON.parse(fs.readFileSync(SNAPSHOT_PATH, 'utf8'));
    return Array.isArray(parsed.notes) ? parsed.notes : [];
  } catch {
    return [];
  }
}

function snapshotSearch(query, limit) {
  const tokens = String(query || '')
    .split(/\s+/)
    .map((t) => t.toLowerCase())
    .filter(Boolean);
  const notes = loadSnapshot();
  if (!tokens.length) {
    return { query, limit, count: 0, results: [], source: 'snapshot' };
  }
  const hits = [];
  for (const note of notes) {
    const haystack = `${note.title || ''}\n${note.body || ''}`.toLowerCase();
    if (!tokens.every((t) => haystack.includes(t))) continue;
    const score = tokens.reduce((sum, t) => sum + haystack.split(t).length - 1, 0);
    hits.push({
      path: note.path,
      title: note.title,
      how_density: note.how_density || '',
      how_executability: note.how_executability || [],
      score,
      related_concepts: note.related_concepts || [],
      graph_links: note.graph_links || [],
    });
  }
  hits.sort((a, b) => b.score - a.score || String(a.path).localeCompare(String(b.path)));
  const results = limit > 0 ? hits.slice(0, limit) : hits;
  return { query, limit, count: results.length, results, source: 'snapshot' };
}

function snapshotGet(relPath) {
  const notes = loadSnapshot();
  const note = notes.find((item) => item.path === relPath);
  if (!note) return null;
  return {
    source: 'snapshot',
    path: note.path,
    title: note.title,
    body: note.body || '',
    how_density: note.how_density || '',
    how_executability: note.how_executability || [],
  };
}

const TRANSPORT_CODES = new Set([
  'ECONNREFUSED',
  'ENOTFOUND',
  'EHOSTUNREACH',
  'ECONNRESET',
  'EPIPE',
  'ETIMEDOUT',
  'EAI_AGAIN',
]);

function taggedError(code, message, extra) {
  const err = new Error(message);
  err.code = code;
  if (extra) Object.assign(err, extra);
  return err;
}

function isUnreachable(err) {
  if (!err) return false;
  if (err.code === 'timeout' || err.message === 'timeout') return true;
  return TRANSPORT_CODES.has(err.code);
}

function httpJson(options) {
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        let payload = null;
        try {
          payload = raw ? JSON.parse(raw) : {};
        } catch {
          reject(taggedError('invalid_json', 'invalid_json'));
          return;
        }
        resolve({ status: res.statusCode || 0, payload });
      });
    });
    req.on('error', reject);
    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(taggedError('timeout', 'timeout'));
    });
    if (options.body) req.write(options.body);
    req.end();
  });
}

async function liveSearch(params, token) {
  const host = String(params.host || DEFAULT_HOST);
  const port = Number(params.port || DEFAULT_PORT);
  const query = String(params.query || '');
  const limit = capLimit(params.limit);
  const body = JSON.stringify({ query, limit });
  const { status, payload } = await httpJson({
    hostname: host,
    port,
    path: '/v1/search',
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
    },
    body,
  });
  if (status === 401) {
    throw taggedError('unauthorized', 'unauthorized');
  }
  if (status !== 200) {
    throw taggedError('http_error', `http_${status}`, { status });
  }
  return payload;
}

async function liveGet(params, token) {
  const host = String(params.host || DEFAULT_HOST);
  const port = Number(params.port || DEFAULT_PORT);
  const rel = String(params.path || '');
  const qs = `/v1/note?path=${encodeURIComponent(rel)}`;
  const { status, payload } = await httpJson({
    hostname: host,
    port,
    path: qs,
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (status === 401) {
    throw taggedError('unauthorized', 'unauthorized');
  }
  if (status === 404) {
    throw taggedError('not_found', 'not_found');
  }
  if (status !== 200) {
    throw taggedError('http_error', `http_${status}`, { status });
  }
  return payload;
}

function snapshotFallbackSearch(query, limit) {
  const fallback = snapshotSearch(query, limit);
  fallback.degraded = true;
  fallback.reason = 'live_unreachable';
  return fallback;
}

function snapshotFallbackGet(rel) {
  const snap = snapshotGet(rel);
  if (!snap) return { error: { code: -32004, message: 'not_found' } };
  snap.degraded = true;
  snap.reason = 'live_unreachable';
  return snap;
}

function liveHttpError(err) {
  const status = Number(err && err.status) || 0;
  return { error: { code: -32002, message: `live_http_${status || 'error'}` } };
}

async function handleSearch(request) {
  const params = request.params || {};
  const token = secretToken(request);
  const query = String(params.query || '');
  const limit = capLimit(params.limit);
  try {
    return await liveSearch(params, token);
  } catch (err) {
    if (err && err.code === 'unauthorized') {
      return { error: { code: -32001, message: 'unauthorized' } };
    }
    if (err && (err.code === 'http_error' || err.code === 'invalid_json')) {
      return liveHttpError(err);
    }
    if (isUnreachable(err)) {
      return snapshotFallbackSearch(query, limit);
    }
    return liveHttpError(err);
  }
}

async function handleGet(request) {
  const params = request.params || {};
  const token = secretToken(request);
  const rel = String(params.path || '');
  try {
    return await liveGet(params, token);
  } catch (err) {
    if (err && err.code === 'unauthorized') {
      return { error: { code: -32001, message: 'unauthorized' } };
    }
    if (err && err.code === 'not_found') {
      return { error: { code: -32004, message: 'not_found' } };
    }
    if (err && (err.code === 'http_error' || err.code === 'invalid_json')) {
      return liveHttpError(err);
    }
    if (isUnreachable(err)) {
      return snapshotFallbackGet(rel);
    }
    return liveHttpError(err);
  }
}

async function dispatch(request) {
  if (request.method === 'search') return handleSearch(request);
  if (request.method === 'get') return handleGet(request);
  return { error: { code: -32601, message: 'Method not found' } };
}

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    reply({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
    return;
  }
  try {
    const result = await dispatch(request);
    if (result && result.error) {
      reply({ jsonrpc: '2.0', id: request.id, error: result.error });
      return;
    }
    reply({ jsonrpc: '2.0', id: request.id, result });
  } catch (err) {
    reply({
      jsonrpc: '2.0',
      id: request.id,
      error: { code: -32603, message: 'internal_error' },
    });
  }
});

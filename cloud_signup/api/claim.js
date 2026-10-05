'use strict';

const crypto = require('node:crypto');
const { validateClaimInput, claimKey } = require('../lib/validate');

const CLAIM_TTL_SEC = 900;
const KEEP_LAST = 50;

function parseBody(raw) {
  if (typeof raw !== 'string') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// The cloud only queues claims. It holds no signing secret, so it cannot (and
// does not) verify the token: the hub does that when it pulls the claim.
function createHandler({
  fetchImpl = (...args) => fetch(...args),
  env = process.env,
  now = Date.now,
  randomId = () => crypto.randomUUID().replace(/-/g, ''),
} = {}) {
  return async function handler(req, res) {
    if (req.method !== 'POST') {
      res.setHeader('Allow', 'POST');
      return res.status(405).json({ error: 'method' });
    }

    const result = validateClaimInput(parseBody(req.body));
    if (!result.ok) return res.status(400).json({ error: result.error });

    const baseUrl = env.UPSTASH_REDIS_REST_URL;
    const token = env.UPSTASH_REDIS_REST_TOKEN;
    if (!baseUrl || !token) return res.status(500).json({ error: 'server_misconfigured' });

    const claim = { id: randomId(), ...result.value, received_at: Math.floor(now() / 1000) };
    const key = claimKey(claim.venue);
    const commands = [
      ['RPUSH', key, JSON.stringify(claim)],
      ['EXPIRE', key, CLAIM_TTL_SEC],
      ['LTRIM', key, -KEEP_LAST, -1],
    ];

    try {
      const upstream = await fetchImpl(`${baseUrl}/pipeline`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(commands),
      });
      if (!upstream.ok) return res.status(502).json({ error: 'upstream' });
      const results = await upstream.json();
      if (!Array.isArray(results) || results.some((r) => r && r.error)) {
        return res.status(502).json({ error: 'upstream' });
      }
    } catch {
      return res.status(502).json({ error: 'upstream' });
    }
    return res.status(200).json({ ok: true, id: claim.id });
  };
}

module.exports = createHandler();
module.exports.createHandler = createHandler;

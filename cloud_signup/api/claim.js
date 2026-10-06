'use strict';

const crypto = require('node:crypto');
const { validateClaimInput, claimKey } = require('../lib/validate');
const { verifySignupToken } = require('../lib/token');
const { redisConfig } = require('../lib/config');

const CLAIM_TTL_SEC = 900;
const KEEP_LAST = 50;
const USED_TTL_SEC = 900;

function parseBody(raw) {
  if (typeof raw !== 'string') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// Queues claims. A claim is accepted only with a valid, unexpired token, and
// each token works once (SET NX); the hub re-verifies when it pulls.
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

    const { baseUrl, token, secret } = redisConfig(env);
    if (!baseUrl || !token || !secret) return res.status(500).json({ error: 'server_misconfigured' });

    const verdict = verifySignupToken(
      secret, result.value.venue, result.value.token, Math.floor(now() / 1000),
    );
    if (verdict !== 'ok') return res.status(400).json({ error: verdict === 'expired' ? 'expired' : 'token' });

    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    const call = (url, payload) => fetchImpl(url, { method: 'POST', headers, body: JSON.stringify(payload) });
    const usedKey = `fitrace:used:${result.value.token}`;

    try {
      const claimed = await call(baseUrl, ['SET', usedKey, '1', 'NX', 'EX', String(USED_TTL_SEC)]);
      if (!claimed.ok) return res.status(502).json({ error: 'upstream' });
      const claimedBody = await claimed.json();
      if (!claimedBody || claimedBody.error) return res.status(502).json({ error: 'upstream' });
      if (claimedBody.result !== 'OK') return res.status(409).json({ error: 'used' });
    } catch {
      return res.status(502).json({ error: 'upstream' });
    }

    const claim = { id: randomId(), ...result.value, received_at: Math.floor(now() / 1000) };
    const key = claimKey(claim.venue);
    const commands = [
      ['RPUSH', key, JSON.stringify(claim)],
      ['EXPIRE', key, CLAIM_TTL_SEC],
      ['LTRIM', key, -KEEP_LAST, -1],
    ];

    let queued = false;
    try {
      const upstream = await call(`${baseUrl}/pipeline`, commands);
      if (upstream.ok) {
        const results = await upstream.json();
        queued = Array.isArray(results) && !results.some((r) => r && r.error);
      }
    } catch {
      queued = false;
    }
    if (!queued) {
      // Release the token so the person can simply retry.
      try { await call(baseUrl, ['DEL', usedKey]); } catch { /* best effort */ }
      return res.status(502).json({ error: 'upstream' });
    }
    return res.status(200).json({ ok: true, id: claim.id });
  };
}

module.exports = createHandler();
module.exports.createHandler = createHandler;

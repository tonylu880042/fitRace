'use strict';

const { verifySignupToken } = require('../lib/token');
const { redisConfig } = require('../lib/config');

const VENUE_RE = /^[A-Za-z0-9_-]{1,40}$/;
const TOKEN_RE = /^[0-9]{1,12}\.[0-9a-f]{2,32}\.[0-9a-f]{16}$/;

// Which stations the hub currently offers. The same token the sign-up claim
// needs gates it: valid, unexpired and not yet used.
function createHandler({ fetchImpl = (...args) => fetch(...args), env = process.env, now = Date.now } = {}) {
  return async function handler(req, res) {
    if (req.method !== 'GET') {
      res.setHeader('Allow', 'GET');
      return res.status(405).json({ error: 'method' });
    }
    const { v: venue, t: token } = req.query || {};
    if (typeof venue !== 'string' || !VENUE_RE.test(venue)) return res.status(400).json({ error: 'venue' });
    if (typeof token !== 'string' || !TOKEN_RE.test(token)) return res.status(400).json({ error: 'token' });

    const { baseUrl, token: redisToken, secret } = redisConfig(env);
    if (!baseUrl || !redisToken || !secret) return res.status(500).json({ error: 'server_misconfigured' });

    const verdict = verifySignupToken(secret, venue, token, Math.floor(now() / 1000));
    if (verdict !== 'ok') return res.status(400).json({ error: verdict === 'expired' ? 'expired' : 'token' });

    const call = async (payload) => {
      const r = await fetchImpl(baseUrl, {
        method: 'POST',
        headers: { Authorization: `Bearer ${redisToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error('upstream');
      const body = await r.json();
      if (!body || body.error) throw new Error('upstream');
      return body.result;
    };

    let snapshot;
    try {
      if (await call(['EXISTS', `fitrace:used:${token}`])) return res.status(409).json({ error: 'used' });
      snapshot = await call(['GET', `fitrace:stations:${venue}`]);
    } catch {
      return res.status(502).json({ error: 'upstream' });
    }

    let stations = [];
    try {
      const parsed = snapshot ? JSON.parse(snapshot) : [];
      if (Array.isArray(parsed)) stations = parsed;
    } catch {
      stations = [];
    }
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({ stations });
  };
}

module.exports = createHandler();
module.exports.createHandler = createHandler;

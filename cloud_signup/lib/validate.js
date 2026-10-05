'use strict';

const NAME_MAX = 20;
const PHOTO_MAX_CHARS = 200 * 1024;
const PHOTO_PREFIX = 'data:image/webp;base64,';

const VENUE_RE = /^[A-Za-z0-9_-]{1,40}$/;
const TOKEN_RE = /^[0-9]{1,12}\.[0-9a-f]{2,32}\.[0-9a-f]{16}$/;
const PHOTO_BODY_RE = /^[A-Za-z0-9+/]+={0,2}$/;

function parseStation(value) {
  if (typeof value === 'number') return Number.isInteger(value) ? value : NaN;
  if (typeof value === 'string' && /^[0-9]{1,3}$/.test(value)) return Number(value);
  return NaN;
}

function validatePhoto(photo) {
  if (photo === undefined || photo === null || photo === '') return { ok: true, value: null };
  if (typeof photo !== 'string' || photo.length > PHOTO_MAX_CHARS) return { ok: false };
  if (!photo.startsWith(PHOTO_PREFIX)) return { ok: false };
  if (!PHOTO_BODY_RE.test(photo.slice(PHOTO_PREFIX.length))) return { ok: false };
  return { ok: true, value: photo };
}

// Returns { ok: true, value } or { ok: false, error: '<field>' }.
function validateClaimInput(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { ok: false, error: 'body' };

  if (typeof body.venue !== 'string' || !VENUE_RE.test(body.venue)) return { ok: false, error: 'venue' };

  const station = parseStation(body.station);
  if (!(station >= 1 && station <= 999)) return { ok: false, error: 'station' };

  if (typeof body.token !== 'string' || !TOKEN_RE.test(body.token)) return { ok: false, error: 'token' };

  if (typeof body.name !== 'string') return { ok: false, error: 'name' };
  const name = body.name.trim();
  const length = [...name].length;
  if (length < 1 || length > NAME_MAX) return { ok: false, error: 'name' };

  const photo = validatePhoto(body.avatar_base64);
  if (!photo.ok) return { ok: false, error: 'photo' };

  return {
    ok: true,
    value: { venue: body.venue, station, token: body.token, name, avatar_base64: photo.value },
  };
}

function claimKey(venue) {
  return `fitrace:claims:${venue}`;
}

module.exports = { validateClaimInput, claimKey, NAME_MAX, PHOTO_MAX_CHARS };

# Cloud sign-up (Vercel)

A tiny page + one API route. Attendees scan the projector QR on their own
phone (mobile data), enter a name and optional photo, and the claim is queued
in Upstash Redis. The hub pulls claims with an outbound HTTPS request; the
cloud never calls the hub and holds no signing secret.

```
cloud_signup/
  public/index.html            sign-up page (strings in public/locales/*.json)
  api/claim.js                 POST /api/claim -> RPUSH claim into Redis
  lib/validate.js              pure validation (unit-tested)
```

## Deploy

1. Create a Vercel project from this repo and set **Root Directory** to
   `cloud_signup/`. No build command; zero npm dependencies.
2. Vercel Marketplace -> add **Upstash Redis** to the project. This sets
   `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`. Also add
   `FITRACE_CLOUD_SIGNUP_SECRET` (same value as the hub).
3. On the hub (systemd unit), set all five variables or the feature stays off:

   | Variable | Value |
   |---|---|
   | `FITRACE_CLOUD_SIGNUP_URL` | the Vercel URL, e.g. `https://fitrace-signup.vercel.app/` |
   | `FITRACE_CLOUD_SIGNUP_SECRET` | random string; set the SAME value in Vercel (signs and verifies QR tokens) |
   | `FITRACE_VENUE_ID` | venue slug, `[A-Za-z0-9_-]{1,40}` |
   | `UPSTASH_REDIS_REST_URL` | same as Vercel |
   | `UPSTASH_REDIS_REST_TOKEN` | same as Vercel |

## Claim format

Page URL: `?v=<venue>&s=<station>&t=<token>`. `POST /api/claim` accepts
`{venue, station, token, name, avatar_base64}`: name 1-20 characters, photo
optional (WebP data URL, at most 200 KB). The token is
`<exp>.<nonce>.<sig>` (valid 300 s, HMAC-SHA256 over venue/station/exp/nonce,
see `lib/token.js`) and works once: it is claimed with
`SET fitrace:used:<token> NX EX 900`, a second use gets HTTP 409. Accepted
claims are pushed to `fitrace:claims:<venue>` (expires after 900 s, last 50
kept). The hub verifies the token again and swaps the projector QR for a fresh
one as soon as it pulls any claim.

## Tests

```
cd cloud_signup && node --test
```

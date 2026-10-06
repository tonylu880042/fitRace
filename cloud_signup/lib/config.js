'use strict';

// Upstash credentials arrive as UPSTASH_REDIS_REST_* or, from the Vercel
// Marketplace integration, KV_REST_API_*. UPSTASH_* wins when both exist.
function redisConfig(env) {
  return {
    baseUrl: env.UPSTASH_REDIS_REST_URL || env.KV_REST_API_URL,
    token: env.UPSTASH_REDIS_REST_TOKEN || env.KV_REST_API_TOKEN,
    secret: env.FITRACE_CLOUD_SIGNUP_SECRET,
  };
}

module.exports = { redisConfig };

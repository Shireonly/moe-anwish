// Cloudflare Pages proxy worker
// Routes WebSocket connections to local tunnel backend
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // WebSocket connections go to local tunnel
    if (path === '/ws') {
      // Try tunnel address first (you'll set this env var in Cloudflare Pages dashboard)
      const backend = env.WS_BACKEND || 'wss://court-maryland-meat-independent.trycloudflare.com/ws';
      return fetch(backend, request);
    }

    // Everything else serves static files
    // Cloudflare Pages serves static files on its own, this is fallback
    return env.ASSETS.fetch(request);
  }
};

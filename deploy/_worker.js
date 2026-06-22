/**
 * milimiki 聊天室 - Cloudflare Worker (Edge Optimized)
 * 
 * 功能：
 *   - 静态资源缓存 + 完整安全响应头
 *   - WebSocket 代理到后端
 *   - 基础速率限制 + 敏感路径阻断
 * 
 * 部署: 放到 deploy/ 目录，通过 CF Pages 或 wrangler 部署
 */

const CONFIG = {
  BACKEND_HOST: undefined,  // WebSocket 后端地址
  RATE_LIMIT_REQUESTS: 120,
  RATE_LIMIT_WINDOW_SEC: 60,
  HTML_CACHE_TTL: 3600,
  STATIC_CACHE_TTL: 31536000,
};

const SECURITY_HEADERS = {
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
  'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' wss: ws:; font-src 'self'; media-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; upgrade-insecure-requests",
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'X-XSS-Protection': '1; mode=block',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), midi=(), payment=(), usb=(), xr-spatial-tracking=()',
};

const COMMON_HEADERS = {
  ...SECURITY_HEADERS,
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Resource-Policy': 'same-origin',
};

const rateLimitMap = new Map();

function checkRateLimit(clientIP) {
  const now = Math.floor(Date.now() / 1000);
  let entry = rateLimitMap.get(clientIP);
  if (!entry || entry.windowStart < now - CONFIG.RATE_LIMIT_WINDOW_SEC) {
    entry = { windowStart: now, count: 0 };
    rateLimitMap.set(clientIP, entry);
  }
  entry.count++;
  if (Math.random() < 0.01) {
    const cutoff = now - CONFIG.RATE_LIMIT_WINDOW_SEC;
    for (const [ip, e] of rateLimitMap) {
      if (e.windowStart < cutoff) rateLimitMap.delete(ip);
    }
  }
  return entry.count <= CONFIG.RATE_LIMIT_REQUESTS;
}

function getCacheHeaders(pathname) {
  const ext = pathname.split('.').pop()?.toLowerCase();
  if (pathname === '/' || ext === 'html' || ext === 'htm') {
    return { 'Cache-Control': `public, max-age=${CONFIG.HTML_CACHE_TTL}`, 'Vary': 'Accept-Encoding' };
  }
  if (['css', 'js', 'svg', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'woff', 'woff2', 'ttf'].includes(ext)) {
    return { 'Cache-Control': `public, max-age=${CONFIG.STATIC_CACHE_TTL}, immutable` };
  }
  return { 'Cache-Control': 'public, max-age=3600' };
}

function errorPage(status, message) {
  return new Response(
    `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>${status} - milimiki</title><style>*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f0f13;color:#e4e4e7;display:flex;align-items:center;justify-content:center;height:100dvh;flex-direction:column;gap:16px}h1{font-size:72px;font-weight:800;background:linear-gradient(135deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}p{color:#a1a1aa;font-size:16px}a{color:#60a5fa;text-decoration:none;font-weight:600}a:hover{text-decoration:underline}</style></head><body><h1>${status}</h1><p>${message}</p><p><a href="/">返回首页</a></p></body></html>`,
    { status, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache', ...COMMON_HEADERS } }
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const pathname = url.pathname;
    const clientIP = request.headers.get('CF-Connecting-IP') || 'unknown';

    if (!checkRateLimit(clientIP)) {
      return new Response('Too Many Requests', { status: 429, headers: { 'Retry-After': '60', ...COMMON_HEADERS } });
    }

    // WebSocket upgrade
    const upgrade = request.headers.get('Upgrade');
    if (upgrade && upgrade.toLowerCase() === 'websocket') {
      return fetch(request);
    }

    // Block sensitive paths
    const blocked = ['/.env', '/.git', '/wp-admin', '/admin.php', '/config.php', '/.well-known', '/phpmyadmin'];
    if (blocked.some(p => pathname.toLowerCase().startsWith(p))) {
      return errorPage(404, '页面不存在');
    }

    // Serve from ASSETS
    try {
      const response = await env.ASSETS.fetch(request);
      const newHeaders = new Headers(response.headers);
      for (const [k, v] of Object.entries(COMMON_HEADERS)) {
        if (v) newHeaders.set(k, v);
      }
      const cacheHeaders = getCacheHeaders(pathname);
      for (const [k, v] of Object.entries(cacheHeaders)) {
        newHeaders.set(k, v);
      }
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders,
      });
    } catch (e) {
      return errorPage(502, '服务器暂时不可用，请稍后重试');
    }
  },
};

/**
 * middleware.js — password gate for the research area.
 *
 * Runs at Vercel's edge before anything is served. Everything under /research
 * requires HTTP Basic auth against RESEARCH_USER / RESEARCH_PASS, set as
 * environment variables in the Vercel dashboard so the password never lives in
 * the repository.
 *
 * This is a privacy control, not a security boundary: it keeps the public out
 * of Grant's research, which is what it is for. Nothing behind it is a secret
 * whose disclosure would be damaging — the film grades stay in Google Sheets
 * and the model's arithmetic is deliberately public.
 *
 * Comparison is constant-time-ish to avoid leaking the password by timing.
 */
export const config = { matcher: ['/research/:path*'] };

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default function middleware(request) {
  const user = process.env.RESEARCH_USER || 'grant';
  const pass = process.env.RESEARCH_PASS;

  // Fail CLOSED. An unset password must lock the area, never open it — the
  // opposite default would silently publish the research on a config mistake.
  if (!pass) {
    return new Response('Research area is not configured (RESEARCH_PASS unset).', {
      status: 503, headers: { 'content-type': 'text/plain' },
    });
  }

  const header = request.headers.get('authorization') || '';
  if (header.startsWith('Basic ')) {
    try {
      const [u, p] = atob(header.slice(6)).split(':');
      if (safeEqual(u || '', user) && safeEqual(p || '', pass)) {
        return; // authorized — continue to the static file
      }
    } catch (_) { /* malformed header falls through to the challenge */ }
  }

  return new Response('Authentication required.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="The Model — Research", charset="UTF-8"',
      'content-type': 'text/plain',
    },
  });
}

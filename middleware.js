// Contraseña de todo el sitio (HTTP Basic). Usuario: cualquiera; clave: env SITE_PASSWORD.
// ponytail: una sola clave compartida; usuarios separados o login real si se comparte con más gente.
export const config = { matcher: '/((?!robots.txt).*)' };

const NOINDEX = { 'X-Robots-Tag': 'noindex, nofollow, noarchive' };

export default function middleware(request) {
  const pass = process.env.SITE_PASSWORD;
  if (!pass) return new Response('Falta configurar SITE_PASSWORD', { status: 503, headers: NOINDEX });
  const auth = request.headers.get('authorization') || '';
  let given = '';
  try { given = atob(auth.replace(/^Basic\s+/i, '')).split(':').slice(1).join(':'); } catch {}
  if (auth && safeEq(given, pass)) return new Response(null, { headers: { 'x-middleware-next': '1' } }); // = next() de @vercel/functions
  return new Response('Acceso restringido', {
    status: 401,
    headers: { ...NOINDEX, 'WWW-Authenticate': 'Basic realm="sic", charset="UTF-8"' },
  });
}

function safeEq(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

// Publicaciones manuales: CRUD de manual.json en el repo privado Korneas/sic-r7k2-data (API de contenidos de GitHub).
// Auth: middleware.js pide HTTP Basic a todo el sitio; aquí se revalida la clave (env SITE_PASSWORD) y el usuario del header
// queda como autor (created_by / updated_by). Token: env SIC_DATA_TOKEN (contents rw en sic-r7k2-data, actions rw en sic-r7k2).
// Tras escribir dispara scrape.yml en modo pipeline para que el puntaje salga en ~3 min (scraper/manual.py).
// Las reglas de validación están duplicadas en web/publicar.html: si cambias una, cambia la otra.
const crypto = require('crypto');

const GH = 'https://api.github.com';
const FILE = '/repos/Korneas/sic-r7k2-data/contents/manual.json';
const DISPATCH = '/repos/Korneas/sic-r7k2/actions/workflows/scrape.yml/dispatches';

// [campo, tipo, mín, máx, etiqueta]; requeridos primero
const REQ = [['price', 'int', 10e6, 50e9, 'Precio'], ['area_m2', 'num', 20, 5000, 'Área construida'],
  ['lat', 'num', 3.28, 3.56, 'Ubicación (latitud)'], ['lng', 'num', -76.62, -76.42, 'Ubicación (longitud)'],
  ['age_years', 'num', 0, 150, 'Antigüedad'], ['bedrooms', 'int', 0, 30, 'Habitaciones'], ['bathrooms', 'int', 0, 30, 'Baños']];
const OPT = [['stratum', 'int', 1, 6, 'Estrato'], ['parking', 'int', 0, 30, 'Parqueaderos'], ['floor', 'int', -5, 100, 'Piso'],
  ['admin_fee', 'int', 0, 50e6, 'Administración'], ['area_lot_m2', 'num', 1, 1e6, 'Área de lote']];
const TXT = [['title', 200, 'Título'], ['neighborhood', 120, 'Barrio'], ['description', 10000, 'Descripción'], ['notes', 5000, 'Notas']];
const isUrl = (u) => /^https?:\/\/\S+$/i.test(u);

function validate(b) {
  const out = {}, errs = [];
  if (b.property_type !== 'apartamento' && b.property_type !== 'casa') errs.push('Tipo: elige apartamento o casa.');
  out.property_type = b.property_type;
  for (const [k, t, lo, hi, name] of REQ.concat(OPT)) {
    const raw = b[k], opt = OPT.some((o) => o[0] === k);
    if (raw == null || raw === '') { if (opt) out[k] = null; else errs.push(`${name}: es obligatorio.`); continue; }
    const v = Number(raw);
    if (!Number.isFinite(v) || (t === 'int' && !Number.isInteger(v))) errs.push(`${name}: debe ser un número${t === 'int' ? ' entero' : ''}.`);
    else if (v < lo || v > hi) errs.push(`${name}: debe estar entre ${lo.toLocaleString('es-CO')} y ${hi.toLocaleString('es-CO')}.`);
    else out[k] = v;
  }
  for (const [k, max, name] of TXT) {
    const v = b[k] == null ? '' : String(b[k]).trim();
    if (v.length > max) errs.push(`${name}: máximo ${max} caracteres.`);
    out[k] = v || null;
  }
  out.url = b.url ? String(b.url).trim() : null;
  if (out.url && !isUrl(out.url)) errs.push('URL de origen: debe empezar por http:// o https://.');
  out.photos = (Array.isArray(b.photos) ? b.photos : []).map((u) => String(u).trim()).filter(Boolean);
  if (out.photos.length > 50 || !out.photos.every(isUrl)) errs.push('Fotos: hasta 50 URLs, cada una con http:// o https://.');
  out.status = b.status || 'active';
  if (!['active', 'sold', 'removed'].includes(out.status)) errs.push('Estado: active, sold o removed.');
  return { out, errs };
}

function gh(path, init = {}) {
  return fetch(GH + path, { ...init, headers: { authorization: `Bearer ${process.env.SIC_DATA_TOKEN}`,
    accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28', 'user-agent': 'sic-manual', ...init.headers } });
}

async function load() {
  const r = await gh(FILE);
  if (r.status === 404) return { list: [], sha: undefined };
  if (!r.ok) throw new Error(`GitHub GET ${r.status}`);
  const j = await r.json();
  return { list: JSON.parse(Buffer.from(j.content, 'base64').toString('utf8') || '[]'), sha: j.sha };
}

// mutate(list) -> resultado o {status, error}; se reintenta una vez si el sha cambió entre leer y escribir
async function write(mutate, msg) {
  for (let i = 0; i < 2; i++) {
    const { list, sha } = await load();
    const res = mutate(list);
    if (res && res.error) return res;
    const r = await gh(FILE, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      message: msg, sha, content: Buffer.from(JSON.stringify(list, null, 1) + '\n').toString('base64') }) });
    if (r.ok) return { status: 200, body: res };
    if (r.status !== 409 && r.status !== 422) throw new Error(`GitHub PUT ${r.status}`);
  }
  return { status: 409, error: 'Otro cambio se guardó al mismo tiempo. Recarga e intenta de nuevo.' };
}

// usuario (antes de ':', recortado, ≤60) si la clave coincide; null si no
function user(req) {
  const m = /^Basic\s+(.+)$/i.exec(String(req.headers.authorization || ''));
  const raw = m ? Buffer.from(m[1], 'base64').toString('utf8') : '', i = raw.indexOf(':');
  const want = Buffer.from(process.env.SITE_PASSWORD || ''), got = Buffer.from(i < 0 ? '' : raw.slice(i + 1));
  if (!want.length || want.length !== got.length || !crypto.timingSafeEqual(want, got)) return null;
  return raw.slice(0, i).trim().slice(0, 60) || 'anónimo';
}

module.exports = async function handler(req, res) {
  res.setHeader('cache-control', 'no-store');
  const send = (status, body) => res.status(status).json(body);
  const by = user(req);
  if (!by) return send(401, { error: 'Contraseña del sitio incorrecta.' });
  try {
    if (req.method === 'GET') return send(200, req.query && req.query.whoami ? { user: by } : (await load()).list);
    let body = req.body || {};
    if (typeof body === 'string') try { body = JSON.parse(body || '{}'); } catch { return send(400, { error: 'JSON inválido.' }); }
    const now = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    let w;
    if (req.method === 'POST' || req.method === 'PUT') {
      const { out, errs } = validate(body);
      if (errs.length) return send(400, { error: errs.join(' '), errors: errs });
      if (req.method === 'POST') {
        const item = { id: crypto.randomUUID(), ...out, created_at: now, updated_at: now, created_by: by, updated_by: by };
        w = await write((list) => { list.push(item); return item; }, `manual: crea ${item.id}`);
      } else {
        w = await write((list) => {
          const i = list.findIndex((x) => x.id === body.id);
          if (i < 0) return { status: 404, error: 'No existe esa publicación.' };
          const { created_at, created_by } = list[i];
          return (list[i] = { id: body.id, ...out, created_at, updated_at: now, created_by, updated_by: by });
        }, `manual: edita ${body.id}`);
      }
    } else if (req.method === 'DELETE') {
      const id = req.query && req.query.id;
      w = await write((list) => {
        const i = list.findIndex((x) => x.id === id);
        if (i < 0) return { status: 404, error: 'No existe esa publicación.' };
        return list.splice(i, 1)[0];
      }, `manual: borra ${id}`);
    } else return send(405, { error: 'Método no permitido.' });
    if (w.error) return send(w.status, { error: w.error });
    const d = await gh(DISPATCH, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ref: 'main', inputs: { mode: 'pipeline' } }) }).catch(() => null);
    return send(200, { item: w.body, dispatched: !!(d && d.ok) });
  } catch (e) {
    return send(502, { error: `No se pudo hablar con GitHub (${e.message}).` });
  }
};

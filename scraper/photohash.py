"""dHash de 64 bits de fotos de listings (T16). Las imágenes se descargan a memoria y se descartan: nunca a disco.

hash_for_pairs(conn, pairs, cfg): para pares ambiguos de dedupe baja las primeras N fotos aún sin hash (caché en la
tabla photo_hash por (listing_id, url)), hasta `dedupe.photo_budget` descargas por corrida, con ≤4 hilos."""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from PIL import Image

MAX_BYTES = 4_000_000


def dhash_image(img):
    """dHash 64 bits: gris 9x8, bit = pixel izquierdo > derecho. Devuelve entero sin signo."""
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    h = 0
    for y in range(8):
        for x in range(8):
            h = (h << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
    return h


def dhash_bytes(data):
    img = Image.open(io.BytesIO(data))
    img.draft("L", (160, 160))  # JPEG: decodifica reducido (rápido)
    return dhash_image(img)


def hamming(a, b):
    return bin(a ^ b).count("1")


def _signed(h):
    return h - (1 << 64) if h >= 1 << 63 else h


def unsigned(h):
    return h + (1 << 64) if h < 0 else h


def thumb_url(url):
    """Ningún CDN admite parámetros de tamaño; pero infocasas (Finca Raíz) negocia contenido y con el header
    Accept: image/webp sirve webp mucho más liviano que el PNG original (ver hash_for_pairs)."""
    return url


def _fetch(client, url):
    """-> ('ok', hash) | ('perm', None) error definitivo (4xx, imagen inválida) | ('net', None) transitorio."""
    try:
        with client.stream("GET", thumb_url(url)) as r:
            if 400 <= r.status_code < 500:
                return "perm", r.status_code
            if r.status_code >= 500:
                return "net", r.status_code
            buf = bytearray()
            for chunk in r.iter_bytes(65536):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return "perm", "big"
        return "ok", dhash_bytes(bytes(buf))
    except httpx.HTTPError as e:
        return "net", type(e).__name__
    except Exception as e:  # imagen corrupta / formato no soportado
        return "perm", type(e).__name__


def load_hashes(conn, drop_generic=8):
    """{listing_id: [hash sin signo en orden de idx]}; descarta hashes que aparecen en > drop_generic ofertas."""
    rows = conn.execute("SELECT listing_id, hash FROM photo_hash WHERE hash IS NOT NULL ORDER BY listing_id, idx").fetchall()
    owners = {}
    for lid, h in rows:
        owners.setdefault(h, set()).add(lid)
    out = {}
    for lid, h in rows:
        if len(owners[h]) <= drop_generic:
            out.setdefault(lid, []).append(unsigned(h))
    return out


def hash_for_pairs(conn, pairs, cfg, only_n=None):
    """pairs: [(id_a, id_b)] por prioridad. Devuelve estadísticas. Tolera errores de red (corta si todo falla)."""
    dc = (cfg or {}).get("dedupe") or {}
    budget, n_photos = int(dc.get("photo_budget", 1500)), int(dc.get("photo_n", 3))
    stats = dict(downloads=0, ok=0, perm_fail=0, net_fail=0, codes={}, aborted=False)
    if budget <= 0 or not pairs:
        return stats
    ids = {i for p in pairs for i in p}
    photos = {}
    for lid, ph in conn.execute(f"SELECT id, photos FROM listing WHERE id IN ({','.join('?' * len(ids))})", list(ids)):
        try:
            photos[lid] = [u for u in json.loads(ph or "[]") if isinstance(u, str)][:n_photos]
        except ValueError:
            photos[lid] = []
    have = {(l, u) for l, u in conn.execute("SELECT listing_id, url FROM photo_hash")}
    def needs(a, b):
        if not photos.get(a) or not photos.get(b):
            return []
        return [(l, i, u) for l in (a, b) for i, u in enumerate(photos[l]) if (l, u) not in have]

    todo, seen = [], set()  # pares con menos descargas pendientes primero; se completan pares enteros
    for a, b in sorted(pairs, key=lambda p: len(needs(*p))):
        need = [t for t in needs(a, b) if t not in seen]
        if not need or len(todo) + len(need) > budget:
            continue
        todo += need
        seen.update(need)
    if not todo:
        return stats
    ua = (cfg or {}).get("scraping", {}).get("user_agent") or "Mozilla/5.0"
    client = httpx.Client(headers={"User-Agent": ua, "Accept": "image/webp,image/*;q=0.8"}, timeout=float(dc.get("timeout", 8)),
                          follow_redirects=True)
    workers = min(4, int(dc.get("workers", 4)))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()
    try:
        with ThreadPoolExecutor(workers) as ex:
            for k in range(0, len(todo), 100):
                chunk = todo[k:k + 100]
                for (lid, idx, url), (kind, val) in zip(chunk, ex.map(lambda t: _fetch(client, t[2]), chunk)):
                    stats["downloads"] += 1
                    if kind == "ok":
                        stats["ok"] += 1
                        conn.execute("INSERT OR REPLACE INTO photo_hash VALUES (?,?,?,?,?)", (lid, idx, url, _signed(val), now))
                    elif kind == "perm":
                        stats["perm_fail"] += 1
                        stats["codes"][str(val)] = stats["codes"].get(str(val), 0) + 1
                        conn.execute("INSERT OR REPLACE INTO photo_hash VALUES (?,?,?,?,?)", (lid, idx, url, None, now))
                    else:
                        stats["net_fail"] += 1
                        stats["codes"][str(val)] = stats["codes"].get(str(val), 0) + 1
                conn.commit()
                if stats["net_fail"] >= 30 and stats["ok"] == 0:
                    stats["aborted"] = True
                    break
                if time.time() - t0 > float(dc.get("photo_seconds", 240)):  # tope de tiempo: lo que falte sigue en la próxima corrida
                    stats["aborted"] = "tiempo"
                    break
    finally:
        client.close()
    stats["seconds"] = round(time.time() - t0, 1)
    return stats

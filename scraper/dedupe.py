"""Deduplicación entre portales y republicadas (PLAN RF3, RF1.4): pone listing.group_id a TODAS las ofertas.

Dos ofertas son la misma propiedad si: misma operación, tipo y habitaciones; mismo estrato y baños si ambas
los reportan; y
  - ambas geo exactas: distancia < 250 m, precio ±5%, área ±5% o ±3 m² (si ambas la tienen);
  - alguna aproximada: mismo barrio del portal (normalizado), ambas con área y estrato, precio ±2%, área ±2%,
    parqueaderos iguales si ambas los reportan.
Una retirada solo empata con ofertas que aparecieron a lo sumo 60 días después de su retiro (republicadas).
Grupos por enlace completo (todos los miembros empatan entre sí, para no encadenar), máximo MAX_GROUP miembros
y máximo MAX_PER_SOURCE activas por portal; group_id = miembro con first_seen más antiguo (empate: id menor).
Candidatos: cubetas (operación, tipo, habitaciones) ordenadas por precio y ventana de ±5% → O(n·w), sin O(n²).
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta

from scraper.db import norm

PRICE_TOL, AREA_TOL, AREA_ABS = 0.05, 0.05, 3.0      # coordenadas exactas en ambas
PRICE_TOL_APPROX, AREA_TOL_APPROX = 0.02, 0.02       # alguna aproximada (solo barrio)
MAX_DIST_M = 250.0
REPUBLISH_DAYS = 60
MAX_GROUP = 6
SAME_SOURCE_TOL = 0.02  # dos activas del mismo portal (repost de otra agencia): precio y área ±2%
NO_HOOD = {"", "na", "n/a", "n a", "sin barrio", "otro", "otros", "cali"}
MAX_PER_SOURCE = 2  # activas de un mismo portal por grupo (las retiradas son reposts y no cuentan)

COLS = ("id, source, operation, property_type, bedrooms, bathrooms, parking, stratum_portal, area_m2, price, "
        "lat, lng, geo_precision, neighborhood_portal, status, first_seen, removed_at")


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _meters(a, b):
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lng"] - b["lng"]) * 111_320 * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot(dx, dy)


def _alive_for(x, other):
    """x retirada solo empata con `other` si other apareció ≤60 días después del retiro de x."""
    if x["status"] == "active" or x["_removed"] is None or other["_first"] is None:
        return True
    return other["_first"] <= x["_removed"] + timedelta(days=REPUBLISH_DAYS)


def _rel(x, y):
    return abs(x - y) / max(x, y)


def _differs(x, y):
    return x is not None and y is not None and x != y


SAME_DIST, DIFF_DIST = 6, 12   # Hamming dHash: foto compartida (≤6) / ninguna foto parecida (>12)
REPOST_TOL = 0.10              # republicada (mismo portal, una retirada) con foto compartida: precio/área ±10%
GENERIC_HASH = 8               # un hash que aparece en > 8 ofertas es logo/plano genérico: se ignora


def _pop(x):
    return bin(x).count("1")


def _photo_ev(a, b):
    """'same' si comparten alguna foto (dHash ≤ SAME_DIST); 'diff' si ambas tienen ≥2 fotos con hash y ninguna
    se parece (> DIFF_DIST); None si no hay evidencia."""
    pa, pb = a.get("_ph") or (), b.get("_ph") or ()
    if not pa or not pb:
        return None
    d = min(_pop(x ^ y) for x in pa for y in pb)
    if d <= SAME_DIST:
        return "same"
    if len(pa) >= 2 and len(pb) >= 2 and d > DIFF_DIST:
        return "diff"
    return None


def _hard(a, b):
    """Reglas duras sin tolerancias: estrato, baños y ventana de republicación."""
    return (_differs(a["stratum_portal"], b["stratum_portal"]) or _differs(a["bathrooms"], b["bathrooms"])
            or not (_alive_for(a, b) and _alive_for(b, a)))


def _exact_ok(a, b, dp, da, ptol, atol):
    if dp > ptol or (da is not None and abs(a["area_m2"] - b["area_m2"]) > max(AREA_ABS, atol * max(a["area_m2"], b["area_m2"]))):
        return False
    return _meters(a, b) < MAX_DIST_M


def _repost_pair(a, b):
    return a["source"] == b["source"] and (a["status"] == "active") != (b["status"] == "active")


def match(a, b):
    """a, b en la misma cubeta (operación, tipo, habitaciones) y con precio. Devuelve un costo (menor = más
    parecidas) o None si no son la misma propiedad. Evidencia de fotos (`_ph`): 'same' relaja la regla estricta
    de ubicación aproximada y habilita republicadas con cambios ≤10%; 'diff' veta el par."""
    if _hard(a, b):
        return None
    ev = _photo_ev(a, b)
    if ev == "diff":
        return None
    same = ev == "same"
    dp = _rel(a["price"], b["price"])
    aa, ab = a["area_m2"], b["area_m2"]
    da = _rel(aa, ab) if aa and ab else None
    repost = same and _repost_pair(a, b)
    ptol, atol = (REPOST_TOL, REPOST_TOL) if repost else (PRICE_TOL, AREA_TOL)
    if a["source"] == b["source"] and a["status"] == b["status"] == "active":
        # mismo portal, ambas vigentes: lo normal es que sean unidades distintas del mismo edificio
        if same:
            if da is None or dp > PRICE_TOL or da > AREA_TOL:
                return None
        elif da is None or dp > SAME_SOURCE_TOL or da > SAME_SOURCE_TOL:
            return None
    bonus = 0.02 if same else 0.0
    if a["geo_precision"] == "exact" and b["geo_precision"] == "exact":
        if not _exact_ok(a, b, dp, da, ptol, atol):
            return None
        return dp + (da or AREA_TOL) + _meters(a, b) / MAX_DIST_M * 0.05 - bonus
    # alguna aproximada: mismo barrio y todo más estricto (salvo foto compartida: tolerancias normales)
    if da is None or not a["_hood"] or a["_hood"] != b["_hood"]:
        return None
    if a["stratum_portal"] is None or b["stratum_portal"] is None or _differs(a["parking"], b["parking"]):
        return None
    tp, ta = (ptol, atol) if same else (PRICE_TOL_APPROX, AREA_TOL_APPROX)
    if dp > tp or da > ta:
        return None
    return dp + da + 0.05 - bonus  # penaliza la falta de coordenada frente a un empate exacto


def ambiguous(a, b):
    """Par que pasa las compuertas básicas (ya mismo op/tipo/habs; precio y área ±5%) pero cuya decisión depende de
    una regla débil: alguna ubicación aproximada (candidato a regla estricta ±2%), o republicada que solo un
    cambio ≤10% separaría. Esos pares son los que vale la pena resolver con fotos."""
    if _hard(a, b):
        return False
    dp = _rel(a["price"], b["price"])
    aa, ab = a["area_m2"], b["area_m2"]
    da = _rel(aa, ab) if aa and ab else None
    both_exact = a["geo_precision"] == "exact" and b["geo_precision"] == "exact"
    if _repost_pair(a, b) and dp <= REPOST_TOL and (da is None or da <= REPOST_TOL):
        if both_exact:
            return not _exact_ok(a, b, dp, da, PRICE_TOL, AREA_TOL) and _meters(a, b) < MAX_DIST_M
        return da is not None and bool(a["_hood"]) and a["_hood"] == b["_hood"]
    if both_exact or dp > PRICE_TOL or da is None or da > AREA_TOL:
        return False
    if a["source"] == b["source"] and a["status"] == b["status"] == "active" and (dp > SAME_SOURCE_TOL or da > SAME_SOURCE_TOL):
        return False
    return (bool(a["_hood"]) and a["_hood"] == b["_hood"] and a["stratum_portal"] is not None
            and b["stratum_portal"] is not None and not _differs(a["parking"], b["parking"]))


def _cluster(rows, amb=None):
    """rows: misma cubeta. Enlace completo aglomerativo: se unen los pares en orden de costo y dos grupos solo
    se fusionan si TODOS sus pares cruzados empatan, el tamaño no pasa de MAX_GROUP y ningún portal queda con
    más de MAX_PER_SOURCE activas. Candidatos por ventana de precio (orden por precio; ±10% por las republicadas).
    `amb`: lista a la que se añaden los pares ambiguos (ids) y los rechazados por el tope por portal."""
    rows = sorted(rows, key=lambda r: r["price"])
    edges, ok = [], set()
    for i, a in enumerate(rows):
        hi = a["price"] / (1 - REPOST_TOL)  # b ≤ hi  ⇔  b - a ≤ tol·b
        for b in rows[i + 1:]:
            if b["price"] > hi:
                break
            c = match(a, b)
            if c is not None:
                edges.append((c, a["id"], b["id"]))
                ok.add((a["id"], b["id"]))
                ok.add((b["id"], a["id"]))
            if amb is not None and ambiguous(a, b):
                amb.append((a["id"], b["id"]))
    by_id = {r["id"]: r for r in rows}
    of = {r["id"]: [r["id"]] for r in rows}  # id -> lista compartida de miembros de su grupo
    rejected = 0
    for _, x, y in sorted(edges):
        gx, gy = of[x], of[y]
        if gx is gy:
            continue
        if len(gx) + len(gy) > MAX_GROUP or not all((u, v) in ok for u in gx for v in gy):
            rejected += 1
            continue
        per = defaultdict(int)
        for m in gx + gy:
            if by_id[m]["status"] == "active":
                per[by_id[m]["source"]] += 1
        if max(per.values(), default=0) > MAX_PER_SOURCE:
            rejected += 1
            if amb is not None:
                amb.append((x, y))
            continue
        gx.extend(gy)
        for m in gy:
            of[m] = gx
    groups = {id(g): g for g in of.values()}
    return [[by_id[m] for m in g] for g in groups.values()], rejected


def _order(r):
    return (r["first_seen"] or "", r["id"])


def group_rows(rows, ph=None, amb=None):
    """Lista de dicts de listing -> ({id: group_id}, fusiones rechazadas). Puro (sin DB) para poder probarlo.
    ph: {id: [hash dHash de sus primeras fotos]}; amb: lista donde se acumulan los pares ambiguos."""
    ph = ph or {}
    buckets, out = defaultdict(list), {}
    for r in rows:
        h = norm(r["neighborhood_portal"])
        r["_hood"] = "" if h in NO_HOOD else h
        r["_first"], r["_removed"] = _ts(r["first_seen"]), _ts(r["removed_at"])
        r["_ph"] = ph.get(r["id"], ())
        if r["price"] and r["operation"] and r["property_type"]:
            buckets[(r["operation"], r["property_type"], r["bedrooms"])].append(r)
        else:
            out[r["id"]] = r["id"]
    rejected = 0
    for rs in buckets.values():
        groups, rej = _cluster(rs, amb)
        rejected += rej
        for g in groups:
            gid = min(g, key=_order)["id"]
            for r in g:
                out[r["id"]] = gid
    return out, rejected


def _load_cfg():
    try:
        import yaml
        return yaml.safe_load(open("config.yaml", encoding="utf-8")) or {}
    except Exception:
        return {}


def run(conn, cfg=None):
    """Agrupa; los pares ambiguos se resuelven con dHash de fotos (scraper/photohash.py) dentro del presupuesto
    `dedupe.photo_budget` de descargas por corrida. Sin red o sin Pillow cae al agrupamiento sin fotos."""
    cfg = cfg if cfg is not None else _load_cfg()
    cur = conn.execute(f"SELECT {COLS} FROM listing")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur]
    ph, amb, pstats = {}, [], {}
    try:
        from scraper import photohash
        conn.execute("SELECT 1 FROM photo_hash LIMIT 1")
        ph = photohash.load_hashes(conn)
        group_rows([dict(r) for r in rows], ph, amb)  # pasada 1: solo para detectar pares ambiguos
        pairs = list(dict.fromkeys(amb))
        pstats = photohash.hash_for_pairs(conn, pairs, cfg)
        pstats["ambiguous"] = len(pairs)
        if pstats.get("ok") or pstats.get("perm_fail"):
            ph = photohash.load_hashes(conn)
        print(f"[dedupe] fotos: {len(pairs)} pares ambiguos, {pstats.get('downloads', 0)} descargas "
              f"({pstats.get('ok', 0)} ok, {pstats.get('perm_fail', 0)} def., {pstats.get('net_fail', 0)} red)")
    except Exception as e:  # red, Pillow, tabla ausente...: se sigue sin fotos
        print(f"[dedupe] fotos omitidas ({type(e).__name__}: {e})")
    groups, rejected = group_rows(rows, ph)
    conn.executemany("UPDATE listing SET group_id=? WHERE id=?", [(g, i) for i, g in groups.items()])
    conn.commit()
    sizes = defaultdict(int)
    for g in groups.values():
        sizes[g] += 1
    multi = {g for g, n in sizes.items() if n > 1}
    grouped = sum(sizes[g] for g in multi)
    pct = 100 * grouped / len(rows) if rows else 0
    hist = defaultdict(int)
    for g in multi:
        hist[sizes[g]] += 1
    print(f"[dedupe] {len(rows)} ofertas, {len(multi)} grupos de ≥2, {grouped} agrupadas ({pct:.1f}%), "
          f"tamaños {dict(sorted(hist.items()))}, {rejected} fusiones rechazadas (enlace completo/tope/portal)")
    return dict(listings=len(rows), groups=len(multi), grouped=grouped, pct=round(pct, 1),
                sizes=dict(sorted(hist.items())), rejected=rejected, photos=pstats)


def canonical_ids(conn, operation="sale"):
    """{group_id: id canónico}: el miembro ACTIVO con first_seen más antiguo (empate: id menor) de cada grupo
    con al menos un activo de esa operación. Es la oferta que se muestra en la web."""
    rows = conn.execute("SELECT id, group_id FROM listing WHERE status='active' AND operation=? "
                        "ORDER BY first_seen, id", (operation,))
    out = {}
    for lid, gid in rows:
        out.setdefault(gid or lid, lid)
    return out


def group_first_seen(conn):
    """{group_id: first_seen más antiguo del grupo (incluye retiradas)}: base del dom de la propiedad."""
    rows = conn.execute("SELECT COALESCE(group_id, id), MIN(first_seen) FROM listing GROUP BY 1")
    return {g: fs for g, fs in rows}


if __name__ == "__main__":
    from scraper import db

    run(db.connect())

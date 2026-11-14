"""Deduplicación entre portales y republicadas (PLAN RF3, RF1.4): pone listing.group_id a TODAS las ofertas.

Dos ofertas son la misma propiedad si: misma operación, tipo y habitaciones; mismo estrato si ambas lo
reportan; área a ±5% o ±3 m² (si ambas la tienen); precio a ±5%; y
  - ambas geo exactas: distancia < 250 m;
  - alguna aproximada: mismo barrio del portal (normalizado) y ambas con área.
Una retirada solo empata con ofertas que aparecieron a lo sumo 60 días después de su retiro (republicadas).
Grupos por union-find; group_id = miembro con first_seen más antiguo (empate: id menor).
Candidatos: cubetas (operación, tipo, habitaciones) ordenadas por precio y ventana de ±5% → O(n·w), sin O(n²).
Si un grupo pasa de MAX_GROUP miembros (encadenamiento), se rehace con precio a ±2% y se avisa.
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta

from scraper.db import norm

PRICE_TOL, PRICE_TOL_STRICT = 0.05, 0.02
AREA_TOL, AREA_ABS = 0.05, 3.0
MAX_DIST_M = 250.0
REPUBLISH_DAYS = 60
MAX_GROUP = 6

COLS = ("id, operation, property_type, bedrooms, stratum_portal, area_m2, price, lat, lng, geo_precision, "
        "neighborhood_portal, status, first_seen, removed_at")


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


def is_same(a, b, price_tol=PRICE_TOL):
    """a, b en la misma cubeta (operación, tipo, habitaciones) y con precio."""
    if a["stratum_portal"] is not None and b["stratum_portal"] is not None and a["stratum_portal"] != b["stratum_portal"]:
        return False
    pa, pb = a["price"], b["price"]
    if abs(pa - pb) > price_tol * max(pa, pb):
        return False
    aa, ab = a["area_m2"], b["area_m2"]
    has_area = bool(aa and ab)
    if has_area and abs(aa - ab) > max(AREA_ABS, AREA_TOL * max(aa, ab)):
        return False
    if a["geo_precision"] == "exact" and b["geo_precision"] == "exact":
        if _meters(a, b) >= MAX_DIST_M:
            return False
    elif not (has_area and a["_hood"] and a["_hood"] == b["_hood"]):
        return False
    return _alive_for(a, b) and _alive_for(b, a)


class _UF:
    def __init__(self, ids):
        self.p = {i: i for i in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _cluster(rows, price_tol):
    """rows: misma cubeta. Ventana deslizante por precio. Devuelve lista de grupos (listas de filas)."""
    rows = sorted(rows, key=lambda r: r["price"])
    uf = _UF(r["id"] for r in rows)
    for i, a in enumerate(rows):
        hi = a["price"] / (1 - price_tol)  # b ≤ hi  ⇔  b - a ≤ tol·b
        for b in rows[i + 1:]:
            if b["price"] > hi:
                break
            if is_same(a, b, price_tol):
                uf.union(a["id"], b["id"])
    groups = defaultdict(list)
    for r in rows:
        groups[uf.find(r["id"])].append(r)
    return list(groups.values())


def _order(r):
    return (r["first_seen"] or "", r["id"])


def group_rows(rows):
    """Lista de dicts de listing -> {id: group_id}. Puro (sin DB) para poder probarlo."""
    buckets, out = defaultdict(list), {}
    for r in rows:
        r["_hood"] = norm(r["neighborhood_portal"])
        r["_first"], r["_removed"] = _ts(r["first_seen"]), _ts(r["removed_at"])
        if r["price"] and r["operation"] and r["property_type"]:
            buckets[(r["operation"], r["property_type"], r["bedrooms"])].append(r)
        else:
            out[r["id"]] = r["id"]
    split = 0
    for rs in buckets.values():
        for g in _cluster(rs, PRICE_TOL):
            if len(g) > MAX_GROUP:
                sub = _cluster(g, PRICE_TOL_STRICT)
                split += 1
                print(f"[dedupe] grupo de {len(g)} (> {MAX_GROUP}) partido con precio ±2% en {len(sub)}: "
                      f"{','.join(sorted(r['id'] for r in g)[:8])}…")
                g_list = sub
            else:
                g_list = [g]
            for sg in g_list:
                gid = min(sg, key=_order)["id"]
                for r in sg:
                    out[r["id"]] = gid
    return out, split


def run(conn):
    rows = [dict(r) for r in conn.execute(f"SELECT {COLS} FROM listing")]
    groups, split = group_rows(rows)
    conn.executemany("UPDATE listing SET group_id=? WHERE id=?", [(g, i) for i, g in groups.items()])
    conn.commit()
    sizes = defaultdict(int)
    for g in groups.values():
        sizes[g] += 1
    multi = {g for g, n in sizes.items() if n > 1}
    grouped = sum(sizes[g] for g in multi)
    pct = 100 * grouped / len(rows) if rows else 0
    print(f"[dedupe] {len(rows)} ofertas, {len(multi)} grupos de ≥2, {grouped} agrupadas ({pct:.1f}%), "
          f"{split} grupos partidos por tamaño")
    return dict(listings=len(rows), groups=len(multi), grouped=grouped, split=split)


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

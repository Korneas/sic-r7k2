"""Snapshot diario por zona en `zone_stats` (PLAN §5.3, §6.2 C3/C5). Uso: python -m scraper.zones

Una fila por (zona, fecha COT, tipo ∈ {apartamento, casa, all}). Zonas: cada barrio IDESC (`barrio_code`)
y cada comuna (`'C' + comuna`), que es el respaldo de los barrios con pocas ofertas (lo elige score.py).
Una sola oferta por grupo de dedupe (la canónica activa; en grupos retirados, la última vista).
- n_sale / median_m2_sale: grupos de venta activos con precio/m² dentro de los límites de cordura.
- n_rent / median_m2_rent: igual para arriendo (precio mensual / área).
- gross_yield = 12·median_m2_rent / median_m2_sale, solo si n_rent ≥ 3 y n_sale ≥ 3.
- median_dom: mediana de días entre el first_seen del grupo y su retiro, en grupos de venta retirados en los
  últimos 90 días (proxy del tiempo de venta).
- absorption: grupos de venta retirados en 30 días / grupos de venta activos.
Idempotente: reescribe las filas de la fecha.
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import yaml

from scraper import db

TYPES = ("apartamento", "casa")
DEFAULT_SANITY = dict(pm_min=800_000, pm_max=40_000_000, rent_pm_min=4_000, rent_pm_max=200_000)


def ts(s):
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def comuna_key(c):
    if c is None or c == "":
        return None
    try:
        c = int(float(c))
    except (TypeError, ValueError):
        pass
    return f"C{c}"


def sanity(cfg):
    return {**DEFAULT_SANITY, **((cfg.get("score") or {}).get("sanity") or {})}


def today_cot(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(cfg.get("timezone", "America/Bogota"))).date().isoformat()


def group_reps(conn, operation):
    """{gid: dict(rep row, active, first_seen (grupo), removed_at (grupo))} para una operación."""
    rows = conn.execute(
        "SELECT l.id, COALESCE(l.group_id, l.id) gid, l.property_type, l.price, l.area_m2, l.status, l.first_seen, "
        "l.last_seen, l.removed_at, g.barrio_code, g.comuna FROM listing l LEFT JOIN listing_geo g "
        "ON g.listing_id = l.id WHERE l.operation = ?", (operation,)).fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[r["gid"]].append(r)
    out = {}
    for gid, ms in groups.items():
        act = [m for m in ms if m["status"] == "active"]
        if act:
            rep = min(act, key=lambda m: (m["first_seen"] or "", m["id"]))
        else:
            rep = max(ms, key=lambda m: (m["last_seen"] or "", m["id"]))
        firsts = [ts(m["first_seen"]) for m in ms if m["first_seen"]]
        rems = [ts(m["removed_at"]) for m in ms if m["removed_at"]]
        out[gid] = dict(rep=rep, active=bool(act), first=min(firsts) if firsts else None,
                        removed=max(rems) if rems and not act else None)
    return out


def _pm(r, lo, hi, min_area):
    a, p = r["area_m2"], r["price"]
    if not a or not p or a < min_area:
        return None
    pm = p / a
    return pm if lo <= pm <= hi else None


def _keys(r):
    ks = []
    if r["barrio_code"]:
        ks.append(str(r["barrio_code"]))
    c = comuna_key(r["comuna"])
    if c:
        ks.append(c)
    return ks


def run(conn, cfg=None, now=None):
    t0 = time.time()
    cfg = cfg or yaml.safe_load(open("config.yaml", encoding="utf-8"))
    now = now or datetime.now(timezone.utc)
    day = today_cot(cfg, now)
    sn = sanity(cfg)
    min_area = cfg.get("min_area_m2", 20)

    sale_pm, rent_pm, doms, rem30 = (defaultdict(list) for _ in range(4))
    for g in group_reps(conn, "sale").values():
        r = g["rep"]
        if r["property_type"] not in TYPES:
            continue
        pm = _pm(r, sn["pm_min"], sn["pm_max"], min_area)
        for k in _keys(r):
            for t in (r["property_type"], "all"):
                if g["active"]:
                    if pm is not None:
                        sale_pm[(k, t)].append(pm)
                elif g["removed"] is not None and pm is not None:
                    age = (now - g["removed"]).days
                    if age <= 90 and g["first"] is not None:
                        doms[(k, t)].append(max(0.0, (g["removed"] - g["first"]).total_seconds() / 86400))
                    if age <= 30:
                        rem30[(k, t)] += [1]
    for g in group_reps(conn, "rent").values():
        r = g["rep"]
        if not g["active"] or r["property_type"] not in TYPES:
            continue
        pm = _pm(r, sn["rent_pm_min"], sn["rent_pm_max"], min_area)
        if pm is None:
            continue
        for k in _keys(r):
            for t in (r["property_type"], "all"):
                rent_pm[(k, t)].append(pm)

    rows = []
    for key in set(sale_pm) | set(rent_pm) | set(doms) | set(rem30):
        s, rr, d = sale_pm.get(key, []), rent_pm.get(key, []), doms.get(key, [])
        ms = float(np.median(s)) if s else None
        mr = float(np.median(rr)) if rr else None
        y = round(12 * mr / ms, 4) if len(s) >= 3 and len(rr) >= 3 else None
        md = float(np.median(d)) if d else None
        ab = round(len(rem30.get(key, [])) / len(s), 4) if s else None
        rows.append((key[0], day, key[1], len(s), ms, len(rr), mr, y, md, ab))
    conn.execute("DELETE FROM zone_stats WHERE date = ?", (day,))
    conn.executemany("INSERT INTO zone_stats (barrio_code, date, type, n_sale, median_m2_sale, n_rent, "
                     "median_m2_rent, gross_yield, median_dom, absorption) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    nb = len({r[0] for r in rows if not r[0].startswith("C")})
    nc = len({r[0] for r in rows if r[0].startswith("C")})
    print(f"[zones] {day}: {len(rows)} filas ({nb} barrios, {nc} comunas) en {time.time() - t0:.1f}s")
    return dict(date=day, rows=len(rows), barrios=nb, comunas=nc)


if __name__ == "__main__":
    c = db.connect()
    db.init(c)
    run(c)

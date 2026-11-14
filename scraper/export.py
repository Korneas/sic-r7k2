"""Export web (docs/data-contract.md §3): escribe web/data/ de forma atómica (carpeta temporal + reemplazo).

    .venv/bin/python -m scraper.export
"""
import hashlib
import importlib.util
import json
import math
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "web" / "data"
LAYERS = ROOT / "data" / "layers"
PREFIX = {"fincaraiz": "fr", "metrocuadrado": "m2", "fr": "fr", "m2": "m2"}


def _j(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _r(v, n):
    return None if v is None else round(v, n)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _round_coords(c, nd):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round_coords(x, nd) for x in c]


def _trend_12m(snaps):
    """snaps: [(date, median_m2)] -> pendiente log anualizada (e^b - 1) de los últimos 12 meses; None si < 4."""
    if not snaps:
        return None
    snaps = sorted(snaps)
    last = datetime.fromisoformat(snaps[-1][0][:10])
    pts = [(datetime.fromisoformat(d[:10]), m) for d, m in snaps if m and m > 0]
    pts = [((t - last).days / 365.0, math.log(m)) for t, m in pts if (last - t).days <= 365]
    if len(pts) < 4:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / den
    return round(math.exp(b) - 1, 4)


MIN_ZONE_N = 5


def _zones(conn, names):
    rows = conn.execute("SELECT * FROM zone_stats WHERE type='all' ORDER BY barrio_code, date").fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["barrio_code"], []).append(r)
    out = {}
    for code, rs in by.items():
        last = rs[-1]
        nm = names.get(code, {})
        ok = (last["n_sale"] or 0) >= MIN_ZONE_N  # ponytail: mediana con < 5 ofertas es ruido; se muestra el conteo, no el valor
        out[code] = {"name": nm.get("name"), "comuna": nm.get("comuna"), "n_sale": last["n_sale"],
                     "median_m2": _r(last["median_m2_sale"], 0) if ok else None, "trend_12m": _trend_12m([(r["date"], r["median_m2_sale"]) for r in rs]),
                     "dom": _r(last["median_dom"], 0), "yield": _r(last["gross_yield"], 4), "n_rent": last["n_rent"]}
    return out


def _barrios(out):
    src = LAYERS / "barrios.geojson"
    names = {}
    if not src.exists():
        return names
    data = json.loads(src.read_text(encoding="utf-8"))
    feats = []
    for f in data["features"]:
        p = f.get("properties") or {}
        code = p.get("barcodigo") or p.get("code")
        if not code or not f.get("geometry"):
            continue
        props = {"code": code, "name": p.get("barnombre") or p.get("name"), "comuna": _int(p.get("barcomuna") if "barcomuna" in p else p.get("comuna"))}
        names[code] = props
        g = f["geometry"]
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": g["type"], "coordinates": _round_coords(g["coordinates"], 4)}})
    _dump(out / "barrios.geojson", {"type": "FeatureCollection", "features": feats})
    return names


def _history(conn, ids):
    q = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT seen_at, price FROM price_change WHERE listing_id IN ({q}) ORDER BY seen_at", ids).fetchall()
    hist = []
    for r in rows:
        if r["price"] is None:
            continue
        if hist and hist[-1]["p"] == r["price"]:
            continue
        hist.append({"d": r["seen_at"][:10], "p": r["price"]})
    return hist


def _copy_layers(out):
    spec = importlib.util.spec_from_file_location("copy_web_layers", ROOT / "scripts" / "copy_web_layers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(out / "layers")


def run(conn, cfg, out_dir=None):
    from scraper import dedupe

    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=out_dir.name + ".tmp-", dir=out_dir.parent))
    try:
        stats = _build(conn, cfg, tmp, dedupe)
        old = None
        if out_dir.exists():
            old = out_dir.with_name(out_dir.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            out_dir.rename(old)
        tmp.rename(out_dir)
        if old:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"[export] {stats['listings']} ofertas, {stats['shards']} shards, {stats['zones']} zonas -> {out_dir}")
    return stats


def _build(conn, cfg, out, dedupe):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    today = now[:10]
    names = _barrios(out)
    zones = _zones(conn, names)
    _dump(out / "zones.json", zones)

    canon = dedupe.canonical_ids(conn, "sale")
    gfs = dedupe.group_first_seen(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listing_geo)")}
    L = {r["id"]: r for r in conn.execute("SELECT * FROM listing")}
    G = {r["listing_id"]: r for r in conn.execute("SELECT * FROM listing_geo")}
    S = {r["listing_id"]: r for r in conn.execute("SELECT * FROM listing_score")}
    members = {}
    for r in L.values():
        members.setdefault(r["group_id"] or r["id"], []).append(r)

    listings, shards, n_scored, skipped = [], {}, 0, 0
    for gid, cid in canon.items():
        r = L[cid]
        g = G.get(cid)
        sc = S.get(cid)
        lat = g["lat"] if g is not None and "lat" in cols and g["lat"] is not None else r["lat"]
        lng = g["lng"] if g is not None and "lng" in cols and g["lng"] is not None else r["lng"]
        if lat is None or lng is None:
            skipped += 1
            continue
        comp = (_j(sc["components"], {}) if sc else {}) or {}
        if not isinstance(comp, dict):
            comp = {}
        pens = (_j(sc["penalties"], []) if sc else []) or []
        cc = comp.get("c") or [0, 0, 0, 0, 0]
        pens = [p for p in pens if isinstance(p, dict)]
        mem = sorted(members.get(gid, [r]), key=lambda x: (x["first_seen"] or "", x["id"]))
        act = [m for m in mem if m["status"] == "active" and m["operation"] == "sale"] or mem
        src = sorted({PREFIX.get(m["source"], m["source"]) for m in act}, key=lambda s: (s != "fr", s))
        fs = (gfs.get(gid) or r["first_seen"] or today)[:10]
        photos = _j(r["photos"], []) or []
        hist = _history(conn, [m["id"] for m in mem])
        if comp.get("pd") is not None:
            pd = comp["pd"]
        else:
            pd = round(hist[-1]["p"] / hist[0]["p"] - 1, 3) if len(hist) > 1 and hist[0]["p"] else None
        dom = comp.get("dom")
        if dom is None:
            dom = (datetime.fromisoformat(today) - datetime.fromisoformat(fs)).days
        score = sc["score"] if sc else None
        if not isinstance(comp, dict):
            comp = {}
        if score is not None:
            n_scored += 1
        comuna = _int(g["comuna"]) if g is not None else None
        pt = (r["property_type"] or "").lower()
        listings.append({
            "id": cid, "t": "a" if pt.startswith("apart") else "c", "p": r["price"], "a": r["area_m2"], "pm": _r(r["price_m2"], 0) and int(round(r["price_m2"])),
            "b": r["bedrooms"], "ba": r["bathrooms"], "pk": r["parking"],
            "e": (g["stratum_official"] if g is not None and g["stratum_official"] is not None else r["stratum_portal"]),
            "age": r["age_years"], "br": (g["barrio"] if g is not None and g["barrio"] else r["neighborhood_portal"]),
            "brc": g["barrio_code"] if g is not None else None, "cm": comuna,
            "lat": round(lat, 5), "lng": round(lng, 5), "gp": "e" if r["geo_precision"] == "exact" else "b",
            "s": _r(score, 1), "c": [round(x or 0) for x in cc],
            "d": _r(comp.get("discount") or 0, 3), "up": _r(comp.get("upside") or 0, 3),
            "conf": _r(sc["confidence"], 2) if sc and sc["confidence"] is not None else 0,
            "tags": comp.get("tags") or [], "pen": pens, "img": photos[0] if photos else None,
            "fs": fs, "pd": _r(pd, 3), "src": src, "dom": dom,
        })

        ar = g["amen_top"] if g is not None and "amen_top" in cols else None
        z = zones.get(g["barrio_code"]) if g is not None else None
        urls = []
        for m in act + [m for m in mem if m not in act]:
            if m["url"] and m["url"] not in urls:
                urls.append(m["url"])
        flip = (_j(sc["flip"], {}) if sc else {}) or {}
        if not isinstance(flip, dict):
            flip = {}
        cmp_ = [c for c in ((_j(sc["comps"], []) if sc else []) or []) if isinstance(c, dict)]
        shards.setdefault(hashlib.md5(cid.encode()).hexdigest()[:2], {})[cid] = {
            "title": r["title"], "desc": r["description"], "photos": photos[:20], "urls": urls,
            "admin": r["admin_fee"], "floor": r["floor"], "area_lot": r["area_lot_m2"], "history": hist,
            "expected_m2": _r(sc["expected_m2"], 0) if sc else None, "arv_m2": _r(sc["arv_m2"], 0) if sc else None,
            "max_offer_70": flip.get("max_offer_70"),
            "comps": cmp_, "comps_info": comp.get("comps_info"),
            "amen": [a for a in (_j(ar, []) or []) if isinstance(a, dict)],
            "risks": [{"k": x.get("k"), "why": x.get("why")} for x in (_j(g["risks"], []) if g is not None else []) or [] if isinstance(x, dict)],
            "projects": (_j(g["projects"], []) if g is not None else []) or [],
            "zone": ({"median_m2": z["median_m2"], "trend_12m": z["trend_12m"], "dom": z["dom"], "yield": z["yield"]} if z else None),
        }

    listings.sort(key=lambda l: (-(l["s"] if l["s"] is not None else -1), l["id"]))
    _dump(out / "listings.json", {"generated_at": now, "listings": listings})
    (out / "detail").mkdir()
    for xx, d in shards.items():
        _dump(out / "detail" / f"{xx}.json", d)

    runs = []
    for r in conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT 20"):
        errs = _j(r["errors"], []) or []
        runs.append({"at": r["started_at"], "source": PREFIX.get(r["source"], r["source"]), "mode": r["mode"],
                     "fetched": r["fetched"], "new": r["new"], "removed": r["removed"], "errors": len(errs), "ok": not errs})
    layers = {}
    mf = LAYERS / "manifest.json"
    if mf.exists():
        layers = {k: v.get("fetched") for k, v in json.loads(mf.read_text(encoding="utf-8")).items() if isinstance(v, dict)}
    cnt = lambda op: conn.execute("SELECT COUNT(*) FROM listing WHERE status='active' AND operation=?", (op,)).fetchone()[0]
    _dump(out / "meta.json", {
        "generated_at": now, "fixture": False, "weights": (cfg.get("score") or {}).get("weights", {}),
        "runs": runs, "layers": layers,
        "counts": {"sale_active": cnt("sale"), "rent_active": cnt("rent"), "groups": len(canon),
                   "exported": len(listings), "scored": n_scored, "no_coords": skipped}})
    if LAYERS.exists():
        _copy_layers(out)
    return {"listings": len(listings), "shards": len(shards), "zones": len(zones)}


if __name__ == "__main__":
    import yaml
    from scraper import db

    c = db.connect()
    db.init(c)
    run(c, yaml.safe_load(open("config.yaml")), sys.argv[1] if len(sys.argv) > 1 else None)

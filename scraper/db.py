"""SQLite `data/sic.db` (PLAN §5.3): esquema, upsert de registros de fuentes y retiradas."""
import json
import os
import sqlite3
import unicodedata
from collections import defaultdict

DB_PATH = os.environ.get("SIC_DB", "data/sic.db")
PREFIX = {"fincaraiz": "fr", "metrocuadrado": "m2"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS listing (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT,
    operation TEXT, property_type TEXT, title TEXT, description TEXT,
    price INTEGER, admin_fee INTEGER, area_m2 REAL, area_lot_m2 REAL, price_m2 REAL,
    bedrooms INTEGER, bathrooms INTEGER, parking INTEGER, floor INTEGER,
    age_years REAL, stratum_portal INTEGER, lat REAL, lng REAL,
    approx_location INTEGER NOT NULL DEFAULT 0, geo_precision TEXT, neighborhood_portal TEXT,
    photos TEXT, features TEXT, published_at TEXT, first_seen TEXT, last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'active', removed_at TEXT, missed_runs INTEGER NOT NULL DEFAULT 0,
    group_id TEXT
);
CREATE INDEX IF NOT EXISTS listing_source_status ON listing(source, status);
CREATE INDEX IF NOT EXISTS listing_group ON listing(group_id);
CREATE TABLE IF NOT EXISTS price_change (
    listing_id TEXT NOT NULL, seen_at TEXT NOT NULL, price INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS price_change_listing ON price_change(listing_id);
CREATE TABLE IF NOT EXISTS listing_geo (
    listing_id TEXT PRIMARY KEY, barrio_code TEXT, barrio TEXT, comuna TEXT, stratum_official INTEGER,
    amenities TEXT, risks TEXT, projects TEXT
);
CREATE TABLE IF NOT EXISTS listing_score (
    listing_id TEXT PRIMARY KEY, computed_at TEXT, score REAL, components TEXT, penalties TEXT,
    expected_m2 REAL, arv_m2 REAL, flip TEXT, comps TEXT, confidence REAL
);
CREATE TABLE IF NOT EXISTS zone_stats (
    barrio_code TEXT NOT NULL, date TEXT NOT NULL, type TEXT NOT NULL,
    n_sale INTEGER, median_m2_sale REAL, n_rent INTEGER, median_m2_rent REAL,
    gross_yield REAL, median_dom REAL, absorption REAL,
    PRIMARY KEY (barrio_code, date, type)
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, mode TEXT, source TEXT,
    fetched INTEGER, requests INTEGER, new INTEGER, updated INTEGER, removed INTEGER,
    errors TEXT, seconds REAL
);
"""

# columnas de listing que vienen directo del registro de la fuente (registro -> columna)
FIELDS = {"source": "source", "source_id": "source_id", "url": "url", "operation": "operation",
          "property_type": "property_type", "title": "title", "description": "description",
          "price": "price", "admin_fee": "admin_fee", "area_m2": "area_m2", "area_lot_m2": "area_lot_m2",
          "bedrooms": "bedrooms", "bathrooms": "bathrooms", "parking": "parking", "floor": "floor",
          "age_years": "age_years", "stratum": "stratum_portal", "lat": "lat", "lng": "lng",
          "neighborhood": "neighborhood_portal"}


# columnas agregadas a listing_geo después del esquema inicial (las llena scraper/geo.py)
GEO_EXTRA = {"lat": "REAL", "lng": "REAL", "amen_score": "REAL", "proj_score": "REAL", "amen_top": "TEXT", "geo_note": "TEXT"}


def connect(path=None):
    path = path or DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(listing_geo)")}
    for col, typ in GEO_EXTRA.items():
        if col not in have:
            conn.execute(f"ALTER TABLE listing_geo ADD COLUMN {col} {typ}")
    conn.commit()


def listing_id(source, source_id):
    return f"{PREFIX[source]}-{source_id}"


def _row(r):
    """Registro de fuente -> dict de columnas de listing."""
    row = {col: r.get(key) for key, col in FIELDS.items()}
    row["source_id"] = str(row["source_id"])
    row["approx_location"] = int(bool(r.get("approx_location")))
    row["photos"] = json.dumps(r.get("photos") or [], ensure_ascii=False)
    row["features"] = json.dumps(r.get("features") or [], ensure_ascii=False)
    row["published_at"] = r.get("published_at")
    a, p = r.get("area_m2"), r.get("price")
    row["price_m2"] = round(p / a, 1) if a and p else None
    return row


def upsert(conn, records, now):
    """Inserta o actualiza. Registra price_change al verla por primera vez y en cada cambio de precio.
    Una retirada que reaparece vuelve a active conservando first_seen."""
    stats = dict(new=0, updated=0, price_changed=0, reactivated=0)
    seen = set()
    for r in records:
        lid = listing_id(r["source"], r["source_id"])
        if lid in seen:
            continue
        seen.add(lid)
        row = _row(r)
        old = conn.execute("SELECT price, status, published_at FROM listing WHERE id=?", (lid,)).fetchone()
        if old is None:
            row.update(id=lid, first_seen=now, last_seen=now, status="active", missed_runs=0)
            cols = ",".join(row)
            conn.execute(f"INSERT INTO listing ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            conn.execute("INSERT INTO price_change VALUES (?,?,?)", (lid, now, row["price"]))
            stats["new"] += 1
            continue
        if row["published_at"] is None:
            row["published_at"] = old["published_at"]
        if old["status"] != "active":
            stats["reactivated"] += 1
        row.update(last_seen=now, status="active", removed_at=None, missed_runs=0)
        conn.execute(f"UPDATE listing SET {','.join(f'{c}=?' for c in row)} WHERE id=?", [*row.values(), lid])
        if row["price"] is not None and row["price"] != old["price"]:
            conn.execute("INSERT INTO price_change VALUES (?,?,?)", (lid, now, row["price"]))
            stats["price_changed"] += 1
        stats["updated"] += 1
    mark_geo_precision(conn)
    conn.commit()
    return stats


def mark_missing(conn, source, seen_ids, mode, now, cfg):
    """Solo en modo full: suma missed_runs a las activas de `source` que no aparecieron
    (seen_ids = ids `fr-…`/`m2-…` o source_ids) y las retira tras N corridas. Devuelve cuántas retiró."""
    if mode != "full":
        return 0
    seen = {s if s.startswith(PREFIX[source] + "-") else listing_id(source, s) for s in map(str, seen_ids)}
    limit = cfg["removed_after_missed_full_runs"]
    removed = 0
    rows = conn.execute("SELECT id, missed_runs FROM listing WHERE source=? AND status='active'", (source,))
    for lid, missed in rows.fetchall():
        if lid in seen:
            continue
        missed += 1
        if missed >= limit:
            conn.execute("UPDATE listing SET missed_runs=?, status='removed', removed_at=? WHERE id=?", (missed, now, lid))
            removed += 1
        else:
            conn.execute("UPDATE listing SET missed_runs=? WHERE id=?", (missed, lid))
    conn.commit()
    return removed


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def mark_geo_precision(conn):
    """exact salvo: sin coords, marcada aproximada por el portal, o coordenada genérica
    (misma coordenada a 5 decimales compartida por ≥3 barrios distintos: punto por defecto del portal)."""
    rows = conn.execute("SELECT id, lat, lng, approx_location, neighborhood_portal FROM listing").fetchall()
    hoods = defaultdict(set)
    key = lambda r: (round(r["lat"], 5), round(r["lng"], 5))
    for r in rows:
        if r["lat"] is not None and r["lng"] is not None:
            hoods[key(r)].add(norm(r["neighborhood_portal"]))
    updates = []
    for r in rows:
        ok = r["lat"] is not None and r["lng"] is not None and not r["approx_location"] and len(hoods[key(r)]) < 3
        updates.append(("exact" if ok else "neighborhood", r["id"]))
    conn.executemany("UPDATE listing SET geo_precision=? WHERE id=?", updates)


def known_ids(conn, source):
    """source_ids de esa fuente ya en la DB (para el modo incremental)."""
    return {r[0] for r in conn.execute("SELECT source_id FROM listing WHERE source=?", (source,))}


def last_full_count(conn, source):
    """fetched de la última corrida full sin errores de esa fuente (None si no hay)."""
    r = conn.execute("SELECT fetched FROM run WHERE source=? AND mode='full' AND fetched>0 "
                     "AND (errors IS NULL OR errors='[]') ORDER BY id DESC LIMIT 1", (source,)).fetchone()
    return r[0] if r else None


def record_run(conn, **kw):
    kw["errors"] = json.dumps(kw.get("errors") or [], ensure_ascii=False)
    conn.execute(f"INSERT INTO run ({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))
    conn.commit()


if __name__ == "__main__":
    c = connect()
    init(c)
    print("ok", DB_PATH)

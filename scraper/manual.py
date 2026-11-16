"""Publicaciones manuales: `data/manual.json` (bajado del repo privado sic-r7k2-data) -> listing con source "manual".

Cada objeto trae `id` (uuid), los campos del registro de fuente (docs/data-contract.md §1), `status`
("active" | "sold" | "removed"), `notes`, `created_at`, `updated_at`, `created_by` y `updated_by` (usuario del HTTP Basic). `notes` NUNCA entra a la DB (es pública):
solo se copian las claves de KEYS. Las vendidas, retiradas o ausentes del archivo quedan status='removed'.
Si el archivo no existe no se toca nada (falta el secret o el repo privado).
"""
import json
import os

from scraper import db

PATH = os.environ.get("SIC_MANUAL", "data/manual.json")
KEYS = ("url", "property_type", "title", "description", "price", "admin_fee", "area_m2", "area_lot_m2", "bedrooms",
        "bathrooms", "parking", "floor", "age_years", "stratum", "neighborhood", "lat", "lng", "photos", "created_by", "updated_by")


def run(conn, now, path=PATH):
    if not os.path.exists(path):
        print(f"[manual] sin {path}, se omite")
        return None
    items = json.load(open(path, encoding="utf-8"))
    recs = []
    for it in items:
        if it.get("status", "active") != "active" or not all(it.get(k) for k in ("id", "price", "area_m2", "lat", "lng")):
            continue
        r = {k: it.get(k) for k in KEYS}
        r.update(source="manual", source_id=str(it["id"]), operation="sale", approx_location=False, features=[],
                 photos=r["photos"] or [], published_at=(it.get("created_at") or now)[:10])
        recs.append(r)
    stats = db.upsert(conn, recs, now)
    keep = [db.listing_id("manual", r["source_id"]) for r in recs]
    cur = conn.execute(f"UPDATE listing SET status='removed', removed_at=? WHERE source='manual' AND status='active' "
                       f"AND id NOT IN ({','.join('?' * len(keep))})", (now, *keep))
    conn.commit()
    stats["removed"] = cur.rowcount
    print(f"[manual] {json.dumps(stats)}")
    return stats

"""Prueba de humo del export con una DB temporal: `.venv/bin/python -m scraper.check_export`."""
import hashlib
import json
import tempfile
from pathlib import Path

from scraper import db, export


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sic-check-export-"))
    conn = db.connect(str(tmp / "t.db"))
    db.init(conn)
    base = dict(status="active", operation="sale", property_type="apartamento", price=300_000_000, area_m2=60.0,
                price_m2=5_000_000, bedrooms=3, photos=json.dumps(["http://img/1.jpg"]), geo_precision="exact",
                lat=3.4, lng=-76.5, first_seen="2026-10-01T00:00:00Z", last_seen="2026-11-14T00:00:00Z", group_id="g1")
    rows = [
        dict(base, id="fr-1", source="fincaraiz", source_id="1", url="https://fr/1"),
        dict(base, id="m2-1", source="metrocuadrado", source_id="1", url="https://m2/1", first_seen="2026-10-05T00:00:00Z"),
        dict(base, id="fr-9", source="fincaraiz", source_id="9", url="https://fr/9", operation="rent", group_id="g9"),
    ]
    for r in rows:
        conn.execute(f"INSERT INTO listing ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
        conn.execute("INSERT INTO listing_geo (listing_id, barrio_code, barrio, comuna, stratum_official, risks, projects, lat, lng, amen_top) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (r["id"], "2205", "Ciudad Jardín", "22", 4, "[]", "[]", 3.41, -76.51, "[]"))
    conn.execute("INSERT INTO price_change VALUES ('fr-1','2026-10-01T00:00:00Z',310000000)")
    conn.execute("INSERT INTO price_change VALUES ('m2-1','2026-10-05T00:00:00Z',310000000)")
    conn.execute("INSERT INTO price_change VALUES ('fr-1','2026-11-01T00:00:00Z',300000000)")
    conn.execute("INSERT INTO listing_score VALUES ('fr-1','2026-11-14',71.234,?,?,5000000,5600000,?,'[]',0.9)",
                 (json.dumps({"c": [80, 70, 50, 60, 50], "discount": 0.18, "upside": 0.25, "tags": ["rebaja"], "pd": -0.032,
                              "dom": 44, "k": 12, "comps_info": "x"}), json.dumps([{"k": "approx", "pts": 0, "why": "w"}]),
                 json.dumps({"max_offer_70": 305000000, "upside_cop": 1})))
    conn.commit()

    out = tmp / "web-data"
    (out).mkdir()
    (out / "stale.json").write_text("{}")
    export.run(conn, {"score": {"weights": {"c1_discount": 1}}}, out_dir=out)

    ls = json.loads((out / "listings.json").read_text())["listings"]
    assert len(ls) == 1 and ls[0]["id"] == "fr-1", ls
    assert ls[0]["src"] == ["fr", "m2"] and ls[0]["s"] == 71.2 and ls[0]["cm"] == 22 and ls[0]["lat"] == 3.41, ls[0]
    shard = out / "detail" / (hashlib.md5(b"fr-1").hexdigest()[:2] + ".json")
    d = json.loads(shard.read_text())["fr-1"]
    assert "https://fr/1" in d["urls"] and "https://m2/1" in d["urls"], d["urls"]
    assert [h["p"] for h in d["history"]] == [310000000, 300000000], d["history"]
    assert d["max_offer_70"] == 305000000
    meta = json.loads((out / "meta.json").read_text())
    assert meta["fixture"] is False and meta["counts"]["rent_active"] == 1
    assert not (out / "stale.json").exists()
    print("ok")


if __name__ == "__main__":
    main()

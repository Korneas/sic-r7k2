"""Autoverificación de scraper/db.py con una DB temporal. Uso: python -m scraper.check_upsert"""
import os
import tempfile

from scraper import db

CFG = {"removed_after_missed_full_runs": 2}


def rec(sid, price=300_000_000, lat=3.40001, lng=-76.53001, hood="A", source="fincaraiz", **kw):
    r = dict(source=source, source_id=sid, url=f"https://x/{sid}", operation="sale", property_type="apartamento",
             title="t", description="d", price=price, admin_fee=None, area_m2=60.0, area_lot_m2=None,
             bedrooms=3, bathrooms=2, parking=1, floor=None, age_years=None, stratum=4, neighborhood=hood,
             lat=lat, lng=lng, approx_location=False, photos=["p1"], features=["f"], published_at=None)
    r.update(kw)
    return r


def main():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(os.path.join(d, "t.db"))
        db.init(conn)
        q = lambda sql, *a: [tuple(r) for r in conn.execute(sql, a)]
        batch = [rec("1"), rec("2", lat=3.41, lng=-76.52), rec("3", source="metrocuadrado", lat=3.42, lng=-76.51)]

        s = db.upsert(conn, batch, "t1")
        assert s["new"] == 3 and s["updated"] == 0, s
        assert {r[0] for r in q("SELECT id FROM listing")} == {"fr-1", "fr-2", "m2-3"}
        assert len(q("SELECT * FROM price_change")) == 3
        assert q("SELECT photos FROM listing WHERE id='fr-1'")[0][0] == '["p1"]'
        assert q("SELECT price_m2 FROM listing WHERE id='fr-1'")[0][0] == 5_000_000

        s = db.upsert(conn, batch, "t2")
        assert s["new"] == 0 and s["updated"] == 3 and s["price_changed"] == 0, s
        assert len(q("SELECT * FROM price_change")) == 3

        s = db.upsert(conn, [rec("1", price=280_000_000)], "t3")
        assert s["price_changed"] == 1, s
        assert q("SELECT price FROM price_change WHERE listing_id='fr-1' ORDER BY seen_at") == [(300_000_000,), (280_000_000,)]

        # incremental no retira; full dos veces sin verla => removed (solo esa fuente)
        assert db.mark_missing(conn, "fincaraiz", {"1"}, "incremental", "t4", CFG) == 0
        assert q("SELECT missed_runs FROM listing WHERE id='fr-2'")[0][0] == 0
        assert db.mark_missing(conn, "fincaraiz", {"1"}, "full", "t4", CFG) == 0
        assert q("SELECT status, missed_runs FROM listing WHERE id='fr-2'")[0] == ("active", 1)
        assert db.mark_missing(conn, "fincaraiz", {"1"}, "full", "t5", CFG) == 1
        assert q("SELECT status, removed_at FROM listing WHERE id='fr-2'")[0] == ("removed", "t5")
        assert q("SELECT status FROM listing WHERE id='m2-3'")[0][0] == "active"
        assert q("SELECT missed_runs FROM listing WHERE id='fr-1'")[0][0] == 0

        # reaparece => active, conserva first_seen
        s = db.upsert(conn, [rec("2", lat=3.41, lng=-76.52)], "t6")
        assert s["reactivated"] == 1 and s["new"] == 0, s
        assert q("SELECT status, first_seen, last_seen, missed_runs, removed_at FROM listing WHERE id='fr-2'")[0] == \
            ("active", "t1", "t6", 0, None)

        # coordenada genérica: misma coordenada en 3 barrios distintos => neighborhood
        g = dict(lat=3.45161, lng=-76.53198)
        db.upsert(conn, [rec("g1", hood="San Fernando", **g), rec("g2", hood="El Ingenio", **g)], "t7")
        assert q("SELECT geo_precision FROM listing WHERE id='fr-g1'")[0][0] == "exact"
        db.upsert(conn, [rec("g3", hood="Ciudad Jardín", **g), rec("g4", hood="san fernando", **g)], "t8")
        assert {r[0] for r in q("SELECT geo_precision FROM listing WHERE id LIKE 'fr-g%'")} == {"neighborhood"}
        db.upsert(conn, [rec("n1", lat=None, lng=None), rec("a1", lat=3.5, lng=-76.5, approx_location=True)], "t9")
        assert q("SELECT geo_precision FROM listing WHERE id IN ('fr-n1','fr-a1')") == [("neighborhood",)] * 2
        assert q("SELECT geo_precision FROM listing WHERE id='fr-1'")[0][0] == "exact"

        # run + guardia de caída
        db.record_run(conn, started_at="t1", mode="full", source="fincaraiz", fetched=100, requests=5,
                      new=1, updated=0, removed=0, errors=[], seconds=1.0)
        db.record_run(conn, started_at="t2", mode="full", source="fincaraiz", fetched=10, requests=5,
                      new=0, updated=0, removed=0, errors=["caída"], seconds=1.0)
        assert db.last_full_count(conn, "fincaraiz") == 100
        assert db.known_ids(conn, "metrocuadrado") == {"3"}
        conn.close()
    print("ok")


if __name__ == "__main__":
    main()

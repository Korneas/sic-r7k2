"""Autoverificación de scraper/zones.py y scraper/score.py con una DB temporal y datos sintéticos.
Uso: python -m scraper.check_score"""
import json
import math
import os
import random
import tempfile
import time
from datetime import datetime, timezone

import yaml

from scraper import db, score, zones

NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
NOW_S = "2026-11-14T20:00:00Z"
GOOD = (3.40, -76.53)
FLOOD = (3.45, -76.50)
FAR = (3.36, -76.56)
NOMIT = {"k": "inund_nomit", "pts": 30, "why": "Zona de amenaza no mitigable por inundación (POT 2014)"}


def off(c, dn, de):
    """Desplaza (lat, lng) dn metros al norte y de al este."""
    return c[0] + dn / 110574.0, c[1] + de / (111320.0 * math.cos(math.radians(3.43)))


def rec(sid, ll, pm, area=70.0, source="fincaraiz", **kw):
    r = dict(source=source, source_id=sid, url=f"https://x/{sid}", operation="sale", property_type="apartamento",
             title="Apartamento", description="bonito", price=int(pm * area), admin_fee=None, area_m2=area,
             area_lot_m2=None, bedrooms=3, bathrooms=2, parking=1, floor=None, age_years=10, stratum=4,
             neighborhood="x", lat=ll[0], lng=ll[1], approx_location=False, photos=[], features=[], published_at=None)
    r.update(kw)
    return r


def neighbors(prefix, center, n=12, r_max=600):
    rnd = random.Random(prefix)
    out = []
    for j in range(n):
        ang, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(100, r_max)
        pm = 5_000_000 * rnd.uniform(0.94, 1.06)
        out.append(rec(f"{prefix}{j}", off(center, d * math.sin(ang), d * math.cos(ang)), pm,
                       area=rnd.uniform(64, 76)))
    return out


def main():
    cfg = yaml.safe_load(open("config.yaml"))
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(os.path.join(d, "t.db"))
        db.init(conn)
        recs = neighbors("g", GOOD) + neighbors("f", FLOOD) + [
            rec("cheap", GOOD, 3_750_000),                                   # 25 % debajo
            rec("cheap", off(GOOD, 20, 0), 3_760_000, source="metrocuadrado"),  # mismo inmueble en M2
            rec("market", off(GOOD, 50, 50), 5_000_000),
            rec("floodcheap", FLOOD, 3_750_000),
            rec("rentassale", off(GOOD, -40, 0), 520_000 / 60, area=60.0),  # precio 520.000 publicado como venta
            rec("typo", off(GOOD, 0, -40), 15_000_000),                       # 3x: |z_MAD| > 4
            rec("far", FAR, 4_000_000),
            rec("mix", off(GOOD, 30, 30), 3_750_000, description="casa multifamiliar, 3 pisos independientes"),
            rec("yumbo", off(GOOD, -30, 30), 3_750_000, title="Apartamento en Venta, Yumbo"),
        ] + [rec(f"b{j}", off(FAR, 2000 + 150 * j, 800), 5_000_000 * (0.95 + 0.02 * j)) for j in range(5)]
        db.upsert(conn, recs, NOW_S)
        # grupos: cada una sola, salvo cheap en FR y M2
        conn.execute("UPDATE listing SET group_id = id")
        conn.execute("UPDATE listing SET group_id = 'fr-cheap' WHERE id IN ('fr-cheap', 'm2-cheap')")
        conn.execute("UPDATE listing SET first_seen = '2026-11-14T19:00:00Z' WHERE id = 'm2-cheap'")
        geo = []
        for lid, lat, lng in conn.execute("SELECT id, lat, lng FROM listing").fetchall():
            sid = lid.split("-", 1)[1]
            if sid.startswith("f") and sid != "far":
                code, com, risks = "F1", 7, [NOMIT]
            elif sid == "far" or sid.startswith("b"):
                code, com, risks = "B1", 22, []
            else:
                code, com, risks = "A1", 17, []
            geo.append((lid, code, f"Barrio {code}", com, 4, json.dumps(risks), lat, lng, 80.0, 40.0))
        conn.executemany("INSERT INTO listing_geo (listing_id, barrio_code, barrio, comuna, stratum_official, risks, "
                         "lat, lng, amen_score, proj_score) VALUES (?,?,?,?,?,?,?,?,?,?)", geo)
        # rent para zone_stats
        db.upsert(conn, [dict(rec(f"r{j}", off(GOOD, 30 * j, 0), 25_000 * (1 + 0.05 * j)), operation="rent")
                         for j in range(4)], NOW_S)
        conn.execute("UPDATE listing SET group_id = id WHERE group_id IS NULL")
        conn.executemany("INSERT INTO listing_geo (listing_id, barrio_code, comuna, lat, lng) VALUES (?,?,?,?,?)",
                         [(f"fr-r{j}", "A1", 17, *off(GOOD, 30 * j, 0)) for j in range(4)])
        p_g1 = conn.execute("SELECT price FROM listing WHERE id='fr-g1'").fetchone()[0]
        conn.execute("INSERT INTO price_change VALUES ('fr-g1', '2026-10-01T00:00:00Z', ?)", (int(p_g1 * 1.1),))
        conn.commit()

        zones.run(conn, cfg, now=NOW)
        zones.run(conn, cfg, now=NOW)  # idempotente
        z = {(r["barrio_code"], r["type"]): r for r in conn.execute("SELECT * FROM zone_stats")}
        assert z[("A1", "apartamento")]["n_rent"] == 4 and z[("A1", "all")]["gross_yield"] > 0
        assert ("C17", "all") in z and z[("A1", "all")]["date"] == "2026-11-14"
        assert conn.execute("SELECT COUNT(*) FROM zone_stats WHERE barrio_code='A1' AND type='all'").fetchone()[0] == 1

        score.run(conn, cfg, now=NOW)
        S = {r["listing_id"]: r for r in conn.execute("SELECT * FROM listing_score")}
        comp = lambda i: json.loads(S[i]["components"])

        cheap, market, flood = S["fr-cheap"], S["fr-market"], S["fr-floodcheap"]
        assert cheap["score"] > market["score"], (cheap["score"], market["score"])
        assert abs(comp("fr-cheap")["discount"] - 0.25) < 0.05, comp("fr-cheap")
        assert "no_oportunidad" not in comp("fr-cheap")["tags"]
        assert flood["score"] < cheap["score"] - 10, (flood["score"], cheap["score"])
        assert any(p["k"] == "inund_nomit" and "no mitigable" in p["why"] for p in json.loads(flood["penalties"]))
        for bad in ("fr-rentassale", "fr-typo"):
            assert S[bad]["score"] is None and "sospechoso" in comp(bad)["tags"], (bad, dict(S[bad]))
        # T13: uso mixto resta y se etiqueta; otro municipio no se puntúa; upside con tope
        assert "mixto" in comp("fr-mix")["tags"] and any(p["k"] == "mixto" for p in json.loads(S["fr-mix"]["penalties"]))
        assert S["fr-mix"]["score"] < cheap["score"], (S["fr-mix"]["score"], cheap["score"])
        assert S["fr-yumbo"]["score"] is None and comp("fr-yumbo")["why_null"] == "fuera_de_cali"
        assert comp("fr-cheap")["upside"] <= cfg["score"]["arv"]["upside_cap"]
        assert cheap["arv_m2"] <= cheap["expected_m2"] * (1 + cfg["score"]["arv"]["cap_vs_expected"]) + 1
        far = comp("fr-far")
        assert S["fr-far"]["score"] is not None and "mismo barrio" in far["comps_info"], far
        assert S["fr-far"]["confidence"] < cheap["confidence"], (S["fr-far"]["confidence"], cheap["confidence"])
        assert S["m2-cheap"]["score"] == cheap["score"] and S["m2-cheap"]["components"] == cheap["components"]
        # el otro miembro del grupo no es comparable de sí mismo
        assert all(c["id"] != "m2-cheap" for c in json.loads(cheap["comps"]))
        assert "rentassale" not in cheap["comps"] and "typo" not in cheap["comps"]
        assert comp("fr-cheap")["k"] >= 12 and cheap["confidence"] == 1.0
        assert json.loads(cheap["flip"])["max_offer_70"] > 0 and cheap["arv_m2"] > cheap["expected_m2"] * 0.9
        assert len(comp("fr-cheap")["c"]) == 5 and "nueva" in comp("fr-cheap")["tags"]
        assert "rebaja" in comp("fr-g1")["tags"] and abs(comp("fr-g1")["pd"] + 0.0909) < 0.001, comp("fr-g1")
        print("cheap", cheap["score"], "market", market["score"], "flood", flood["score"],
              "far", S["fr-far"]["score"], S["fr-far"]["confidence"], far["comps_info"])
        print("components", cheap["components"])

    # rendimiento: ~10k ventas activas
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(os.path.join(d, "p.db"))
        db.init(conn)
        rnd = random.Random(1)
        recs = []
        for j in range(12000):
            ll = (3.35 + rnd.random() * 0.15, -76.56 + rnd.random() * 0.08)
            recs.append(rec(f"p{j}", ll, 5_000_000 * rnd.uniform(0.6, 1.4), area=rnd.uniform(40, 200),
                            property_type=rnd.choice(["apartamento", "casa"]), stratum=rnd.randint(2, 6)))
        db.upsert(conn, recs, NOW_S)
        conn.execute("UPDATE listing SET group_id = id")
        conn.execute("INSERT INTO listing_geo (listing_id, barrio_code, comuna, lat, lng, amen_score, proj_score, risks) "
                     "SELECT id, 'P' || CAST((lat * 100) AS INT) || CAST((-lng * 100) AS INT), 1, lat, lng, 50, 0, '[]' "
                     "FROM listing")
        conn.commit()
        t = time.time()
        zones.run(conn, cfg, now=NOW)
        r = score.run(conn, cfg, now=NOW)
        secs = time.time() - t
        print(f"perf: 12000 ventas en {secs:.1f}s", r)
        assert secs < 60
    print("ok")


if __name__ == "__main__":
    main()

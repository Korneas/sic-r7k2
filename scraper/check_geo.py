"""Autoverificación de scraper/geo.py con una DB temporal y puntos sintéticos. Uso: python -m scraper.check_geo"""
import json
import os
import tempfile

import shapely
import yaml
from shapely.geometry import shape

from scraper import db, geo


def rec(sid, lat, lng, hood=None, desc="d", approx=False, title="t"):
    return dict(source="fincaraiz", source_id=sid, url=f"https://x/{sid}", operation="sale", property_type="apartamento",
                title=title, description=desc, price=300_000_000, admin_fee=None, area_m2=60.0, area_lot_m2=None,
                bedrooms=3, bathrooms=2, parking=1, floor=None, age_years=None, stratum=4, neighborhood=hood,
                lat=lat, lng=lng, approx_location=approx, photos=[], features=[], published_at=None)


def inside(layer, pred=lambda p: True):
    for f in json.load(open(f"data/layers/{layer}.geojson"))["features"]:
        if pred(f["properties"]):
            p = shapely.point_on_surface(shape(f["geometry"]))
            return f["properties"], p.y, p.x
    raise AssertionError(layer)


def main():
    cfg = yaml.safe_load(open("config.yaml"))
    menga, mlat, mlng = inside("barrios", lambda p: p["barnombre"] == "Menga")
    _, flat, flng = inside("inundacion_no_mitigable")
    uni = next(m for m in yaml.safe_load(open("config/anchors.yaml"))["malls"] if m["name"] == "Unicentro")
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(os.path.join(d, "t.db"))
        db.init(conn)
        db.upsert(conn, [
            rec("barrio", mlat, mlng, "Menga"),
            rec("flood", flat, flng),
            rec("mall", uni["lat"] + 0.0002, uni["lng"]),                          # ~22 m de Unicentro
            rec("rural", 3.47, -76.595),                                            # ladera occidental
            rec("byname", None, None, "urbanizacion ciudad jardin"),                # sin coordenada
            rec("wrong", mlat, mlng, "Ciudad Jardín"),                              # punto en Menga, portal dice Ciudad Jardín
            rec("legal", mlat + 0.001, mlng, "Menga", desc="Se venden derechos de posesión"),
            # T13: M2 sin ubicación = (0, 0) y barrio "Na"; el barrio sale del título (IDESC o centroide de config)
            rec("titulo", 0.0, 0.0, "Na", title="Apartamento en Venta, Menga, Cali"),
            rec("centro", None, None, "Bochalema", title="Apartamento en venta en Bochalema, Cali"),
        ], "t0")
        geo.run(conn, cfg)
        g = {r["listing_id"]: r for r in conn.execute("SELECT * FROM listing_geo")}
        prec = dict(conn.execute("SELECT id, geo_precision FROM listing").fetchall())
        risks = lambda i: {r["k"]: r for r in json.loads(g[i]["risks"])}

        assert g["fr-barrio"]["barrio_code"] == menga["barcodigo"], dict(g["fr-barrio"])
        assert g["fr-barrio"]["stratum_official"] is not None
        assert risks("fr-flood")["inund_nomit"]["pts"] == 30
        mall = json.loads(g["fr-mall"]["amenities"])["mall"]
        assert mall["d"][0] < 100, mall
        assert any(t["cat"] == "mall" and t["name"] == "Unicentro" for t in json.loads(g["fr-mall"]["amen_top"]))
        assert g["fr-mall"]["amen_score"] > g["fr-rural"]["amen_score"], (g["fr-mall"]["amen_score"], g["fr-rural"]["amen_score"])
        assert g["fr-byname"]["barrio"] == "Urbanización Ciudad Jardín" and g["fr-byname"]["lat"] is not None
        assert "approx" in risks("fr-byname")
        assert prec["fr-wrong"] == "neighborhood" and g["fr-wrong"]["barrio"] == "Urbanización Ciudad Jardín"
        assert json.loads(g["fr-wrong"]["geo_note"])["orig"][0] == mlat
        assert prec["fr-barrio"] == "exact" and "approx" not in risks("fr-barrio")
        assert "derechos" in risks("fr-legal")["legal"]["why"]
        assert 0 <= g["fr-mall"]["proj_score"] <= 100
        assert g["fr-titulo"]["barrio_code"] == menga["barcodigo"] and prec["fr-titulo"] == "neighborhood"
        assert json.loads(g["fr-titulo"]["geo_note"])["src"] == "titulo"
        bl, bg = cfg["geo"]["centroids"]["bochalema"]
        assert abs(g["fr-centro"]["lat"] - bl) < 1e-4 and abs(g["fr-centro"]["lng"] - bg) < 1e-4, dict(g["fr-centro"])
        assert json.loads(g["fr-centro"]["geo_note"])["src"] == "centroide" and "approx" in risks("fr-centro")
        # idempotente
        geo.run(conn, cfg)
        assert conn.execute("SELECT COUNT(*) FROM listing_geo").fetchone()[0] == 9
        assert "orig" in json.loads(conn.execute("SELECT geo_note FROM listing_geo WHERE listing_id='fr-wrong'").fetchone()[0])
        print("amen_score unicentro", g["fr-mall"]["amen_score"], "rural", g["fr-rural"]["amen_score"])
    print("ok")


if __name__ == "__main__":
    main()

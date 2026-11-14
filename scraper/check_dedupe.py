"""Autoverificación de scraper/dedupe.py con una DB temporal. Uso: python -m scraper.check_dedupe"""
import os
import random
import tempfile
import time

from scraper import db, dedupe

T1, T2, T3, T4 = "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z", "2026-11-04T00:00:00Z"
T_LATE = "2027-03-01T00:00:00Z"  # > 60 días después de T2


def rec(sid, source="fincaraiz", price=300_000_000, area=60.0, lat=3.40001, lng=-76.53001, hood="El Ingenio", **kw):
    r = dict(source=source, source_id=sid, url=f"https://x/{sid}", operation="sale", property_type="apartamento",
             title="t", description="d", price=price, admin_fee=None, area_m2=area, area_lot_m2=None,
             bedrooms=3, bathrooms=2, parking=1, floor=None, age_years=None, stratum=4, neighborhood=hood,
             lat=lat, lng=lng, approx_location=False, photos=[], features=[], published_at=None)
    r.update(kw)
    return r


def main():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(os.path.join(d, "t.db"))
        db.init(conn)
        g = lambda lid: conn.execute("SELECT group_id FROM listing WHERE id=?", (lid,)).fetchone()[0]

        db.upsert(conn, [
            # misma propiedad en FR y M2 (~60 m, precio +2%, área +2 m², barrio escrito distinto)
            rec("1"), rec("1", source="metrocuadrado", price=306_000_000, area=62, lat=3.40055, hood="el ingenio"),
            # mismo edificio, misma área, precio distinto (> 5%) → otra unidad
            rec("2", price=340_000_000),
            # otras habitaciones
            rec("3", bedrooms=2),
            # ubicación aproximada: mismo barrio (sin tildes/mayúsculas), misma área y precio → agrupa
            rec("10", price=500_000_000, area=110, lat=3.37, lng=-76.54, hood="Ciudad Jardín"),
            rec("10", source="metrocuadrado", price=495_000_000, area=108, lat=None, lng=None, hood="CIUDAD JARDIN"),
            # aproximada en otro barrio → no agrupa
            rec("11", source="metrocuadrado", price=500_000_000, area=110, lat=None, lng=None, hood="Pance"),
            # aproximada sin área → no agrupa
            rec("12", source="metrocuadrado", price=500_000_000, area=None, lat=None, lng=None, hood="Ciudad Jardin"),
            # para la republicada
            rec("20", price=800_000_000, area=150, lat=3.45, lng=-76.50, hood="Santa Teresita", bedrooms=4),
            # retirada que reaparece con otro id > 60 días después → no agrupa
            rec("30", price=600_000_000, area=120, lat=3.46, lng=-76.51, hood="San Fernando", bedrooms=5),
        ], T1)
        db.mark_missing(conn, "fincaraiz", ["1", "2", "3", "10"], "full", T2, {"removed_after_missed_full_runs": 1})
        assert conn.execute("SELECT status FROM listing WHERE id='fr-20'").fetchone()[0] == "removed"
        # republicada en el mismo portal con otro id, dentro de 60 días
        db.upsert(conn, [rec("21", price=790_000_000, area=150, lat=3.4501, lng=-76.50, hood="Santa Teresita",
                             bedrooms=4)], T3)
        # republicada > 60 días después del retiro → no agrupa
        db.upsert(conn, [rec("31", price=600_000_000, area=120, lat=3.46, lng=-76.51, hood="San Fernando",
                             bedrooms=5)], T_LATE)
        # aproximada estricta (±2%), tope por portal y baños distintos
        db.upsert(conn, [
            rec("40", price=400_000_000, area=90, lat=None, lng=None, hood="Valle del Lili"),
            rec("40", source="metrocuadrado", price=404_000_000, area=91, lat=None, lng=None, hood="Valle del Lili"),
            rec("41", source="metrocuadrado", price=412_000_000, area=90, lat=None, lng=None, hood="Valle del Lili"),
            *[rec(f"5{i}", price=700_000_000, area=200, lat=3.47, lng=-76.49, hood="X") for i in range(3)],
            rec("60", price=900_000_000, area=99, lat=3.48, lng=-76.48, hood="Y", bathrooms=2),
            rec("60", source="metrocuadrado", price=900_000_000, area=99, lat=3.48, lng=-76.48, hood="Y", bathrooms=3),
        ], T4)
        # cadena: 8 unidades a 3% de precio una de otra → sin encadenar
        db.upsert(conn, [rec(f"c{i}", source="metrocuadrado", price=int(200_000_000 * 1.03 ** i), area=70,
                             lat=3.43, lng=-76.55, hood="Chain") for i in range(8)], T4)

        s = dedupe.run(conn)
        assert g("fr-1") == g("m2-1") == "fr-1", (g("fr-1"), g("m2-1"))
        assert g("fr-2") == "fr-2"
        assert g("fr-3") == "fr-3"
        assert g("fr-10") == g("m2-10") == "fr-10"
        assert g("m2-11") == "m2-11" and g("m2-12") == "m2-12"
        assert g("fr-20") == g("fr-21") == "fr-20"
        assert g("fr-30") == "fr-30" and g("fr-31") == "fr-31"
        chain = [g(f"m2-c{i}") for i in range(8)]
        assert all(chain.count(x) <= dedupe.MAX_GROUP for x in chain), chain
        assert all(chain.count(x) <= 2 for x in chain), chain  # enlace completo: c_i solo empata con vecinos
        assert g("fr-40") == g("m2-40") and g("m2-41") == "m2-41"  # aproximada: +3% de precio ya no agrupa
        trio = [g(f"fr-5{i}") for i in range(3)]
        assert max(trio.count(x) for x in trio) == 2, trio  # máx. 2 activas del mismo portal por grupo
        assert g("fr-60") != g("m2-60")  # baños distintos
        assert conn.execute("SELECT COUNT(*) FROM listing WHERE group_id IS NULL").fetchone()[0] == 0

        can = dedupe.canonical_ids(conn)
        assert can["fr-20"] == "fr-21", can  # canónico = el activo; el grupo conserva el id del más antiguo
        assert can["fr-1"] == "fr-1"
        assert "fr-20" not in can.values()
        fs = dedupe.group_first_seen(conn)
        assert fs["fr-20"] == T1 and fs["fr-1"] == T1 and fs["fr-31"] == T_LATE, fs

        # idempotente
        dedupe.run(conn)
        assert g("fr-21") == "fr-20"

        # ---- T16: fotos (hashes sintéticos inyectados en photo_hash, sin red) ----
        NOPH = {"dedupe": {"photo_budget": 0}}
        H1, H2, H3, H4 = 0x0F0F0F0F0F0F0F0F, 0xF0F0F0F0F0F0F0F0, 0x00FF00FF00FF00FF, 0x123456789ABCDEF0
        flip = lambda h, n: h ^ ((1 << n) - 1)  # n bits distintos
        # a) aproximada +4%/+4% (la regla ±2% la rechaza): una foto idéntica la agrupa
        # b) aproximada +1% que sí agrupa sin fotos, pero con fotos todas distintas (≥2 c/u) se separa
        # c) igual que (a) pero sin hashes: no agrupa (control)
        # d) republicada mismo portal, precio +8%, área +8%, con foto compartida (flip 3 bits) → agrupa
        # e) republicada +8% sin hashes → no agrupa
        db.upsert(conn, [
            rec("70", price=500_000_000, area=100, lat=None, lng=None, hood="Pance", bedrooms=6),
            rec("70", source="metrocuadrado", price=520_000_000, area=104, lat=None, lng=None, hood="pance", bedrooms=6),
            rec("71", price=500_000_000, area=100, lat=None, lng=None, hood="Bochalema", bedrooms=7),
            rec("71", source="metrocuadrado", price=505_000_000, area=101, lat=None, lng=None, hood="Bochalema", bedrooms=7),
            rec("72", price=500_000_000, area=100, lat=None, lng=None, hood="Pance", bedrooms=8),
            rec("72", source="metrocuadrado", price=520_000_000, area=104, lat=None, lng=None, hood="pance", bedrooms=8),
            rec("80", price=500_000_000, area=100, lat=3.41, lng=-76.52, hood="Z", bedrooms=9),
            rec("82", price=500_000_000, area=100, lat=3.42, lng=-76.52, hood="Z2", bedrooms=10),
        ], T3)
        db.upsert(conn, [rec("81", price=540_000_000, area=108, lat=3.41, lng=-76.52, hood="Z", bedrooms=9),
                         rec("83", price=540_000_000, area=108, lat=3.42, lng=-76.52, hood="Z2", bedrooms=10)], T4)
        db.mark_missing(conn, "fincaraiz", ["81", "83"], "full", T4, {"removed_after_missed_full_runs": 1})
        ph = lambda lid, hs: conn.executemany("INSERT INTO photo_hash VALUES (?,?,?,?,?)", [
            (lid, i, f"u{lid}{i}", h - (1 << 64) if h >= 1 << 63 else h, T4) for i, h in enumerate(hs)])
        ph("fr-70", [H1, H2]); ph("m2-70", [H3, flip(H1, 3)])
        ph("fr-71", [H1, H2]); ph("m2-71", [H3, H4])
        ph("fr-80", [H1, H2]); ph("fr-81", [flip(H2, 2), H4])
        conn.commit()
        dedupe.run(conn, NOPH)
        assert g("fr-70") == g("m2-70"), (g("fr-70"), g("m2-70"))      # foto compartida anula el ±2%
        assert g("fr-71") != g("m2-71")                                 # fotos distintas separan un par que agrupaba
        assert g("fr-72") != g("m2-72")                                 # control sin hashes: +4% no agrupa
        assert g("fr-80") == g("fr-81")                                 # republicada +8% con foto compartida
        assert g("fr-82") != g("fr-83")                                 # republicada +8% sin evidencia: no agrupa
        # el filtro de hashes genéricos: el mismo hash en > 8 ofertas se ignora
        from scraper import photohash
        assert 0 not in photohash.load_hashes(conn)
        # dHash offline: redimensionada ≈ misma; otra imagen, lejos
        from PIL import Image, ImageDraw
        def synth(seed, size=(320, 240)):
            r = random.Random(seed)
            im = Image.new("RGB", size, (r.randint(0, 255),) * 3)
            dr = ImageDraw.Draw(im)
            for _ in range(25):
                x, y = r.randint(0, size[0]), r.randint(0, size[1])
                dr.ellipse([x, y, x + r.randint(20, 120), y + r.randint(20, 120)], fill=tuple(r.randint(0, 255) for _ in range(3)))
            return im
        im = synth(1)
        d_same = photohash.hamming(photohash.dhash_image(im), photohash.dhash_image(im.resize((200, 150))))
        d_diff = photohash.hamming(photohash.dhash_image(im), photohash.dhash_image(synth(2)))
        assert d_same <= 6, d_same
        assert d_diff > 12, d_diff
        print(f"dHash: misma {d_same} bits, distinta {d_diff} bits")

        # escala: 25k ofertas sintéticas
        rnd = random.Random(1)
        rows = []
        for i in range(25_000):
            rows.append(dict(id=f"x-{i}", source=rnd.choice(["fincaraiz", "metrocuadrado"]), bathrooms=rnd.randint(1, 4), parking=rnd.randint(0, 2), operation=rnd.choice(["sale", "sale", "sale", "rent"]),
                             property_type=rnd.choice(["apartamento", "casa"]), bedrooms=rnd.randint(1, 5),
                             stratum_portal=rnd.randint(2, 6), area_m2=rnd.uniform(40, 250),
                             price=rnd.randint(100, 1500) * 1_000_000, lat=3.35 + rnd.random() * 0.15,
                             lng=-76.56 + rnd.random() * 0.1, geo_precision=rnd.choice(["exact"] * 4 + ["neighborhood"]),
                             neighborhood_portal=f"B{rnd.randint(1, 300)}", status="active",
                             first_seen=T1, removed_at=None))
        t = time.time()
        dedupe.group_rows(rows)
        dt = time.time() - t
        print(f"25k sintéticas: {dt:.1f}s")
        assert dt < 60, dt
    print("ok")


if __name__ == "__main__":
    main()

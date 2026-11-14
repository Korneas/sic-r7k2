#!/usr/bin/env python3
"""DEV ONLY: genera datos FALSOS para web/data/ y poder trabajar la UI sin el scraper.

    python3 web/dev/make_fixture.py            # 3000 ofertas (por defecto)
    python3 web/dev/make_fixture.py --n 9000   # prueba de rendimiento

Escribe web/data/listings.json, web/data/detail/<md5(id)[:2]>.json, web/data/zones.json
web/data/meta.json y web/data/barrios.geojson siguiendo docs/data-contract.md §3. El paso real de export
(`python -m scraper.export`) SOBREESCRIBE web/data/: no edites esos archivos a mano.
meta.json lleva "fixture": true para que la UI muestre el aviso "datos de prueba".

Incluye casos extremos a propósito: nombres de barrio muy largos, área faltante,
puntaje nulo, sin fotos, muchas penalizaciones, precios sospechosos.
Solo stdlib. Determinista (semilla fija).
"""
import argparse
import hashlib
import json
import math
import random
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"

# (nombre, comuna, lat, lng, estrato, precio/m² base en millones)
BARRIOS = [
    ("Ciudad Jardín", 22, 3.366, -76.535, 6, 7.2),
    ("Urbanización Ciudad Jardín Sector Pance Etapa II Conjunto Residencial Los Almendros del Sur", 22, 3.362, -76.528, 6, 7.6),
    ("El Ingenio", 17, 3.386, -76.528, 5, 5.6),
    ("Valle del Lili", 17, 3.373, -76.518, 4, 4.6),
    ("El Caney", 17, 3.392, -76.512, 4, 4.3),
    ("Capri", 17, 3.392, -76.536, 5, 5.3),
    ("Primero de Mayo", 17, 3.398, -76.529, 4, 4.4),
    ("El Limonar", 17, 3.397, -76.542, 5, 5.4),
    ("Meléndez", 18, 3.381, -76.546, 3, 3.6),
    ("Nápoles", 18, 3.410, -76.548, 3, 3.5),
    ("Pampalinda", 19, 3.407, -76.541, 4, 4.5),
    ("El Refugio", 19, 3.416, -76.546, 5, 5.5),
    ("Tequendama", 19, 3.421, -76.536, 4, 4.8),
    ("Los Cámbulos", 19, 3.426, -76.533, 4, 4.9),
    ("San Fernando Viejo", 19, 3.437, -76.543, 4, 5.0),
    ("El Lido", 19, 3.430, -76.540, 4, 4.7),
    ("Camino Real", 19, 3.412, -76.530, 4, 4.2),
    ("San Antonio", 3, 3.447, -76.537, 3, 4.6),
    ("El Peñón", 3, 3.452, -76.545, 5, 6.1),
    ("Granada", 2, 3.462, -76.534, 5, 6.3),
    ("Versalles", 2, 3.461, -76.527, 4, 5.2),
    ("Normandía", 2, 3.456, -76.550, 5, 6.6),
    ("Juanambú", 2, 3.459, -76.541, 5, 6.0),
    ("Santa Mónica Residencial", 2, 3.469, -76.530, 5, 6.0),
    ("Chipichape", 2, 3.476, -76.528, 5, 5.9),
    ("La Flora", 2, 3.484, -76.522, 5, 5.4),
    ("Vipasa", 2, 3.482, -76.514, 4, 4.6),
    ("Prados del Norte", 2, 3.475, -76.516, 4, 4.8),
    ("Floralia", 6, 3.486, -76.497, 3, 2.9),
    ("Los Guaduales", 6, 3.488, -76.506, 3, 3.0),
    ("Salomia", 4, 3.466, -76.510, 3, 3.1),
    ("Alfonso López", 7, 3.470, -76.489, 2, 2.6),
    ("Guayaquil", 9, 3.441, -76.521, 3, 3.3),
    ("El Guabal", 10, 3.421, -76.516, 3, 3.2),
    ("Santa Elena", 10, 3.430, -76.514, 3, 3.4),
    ("Ciudad Modelo", 10, 3.416, -76.522, 3, 3.3),
    ("Villa del Lago", 13, 3.426, -76.490, 2, 2.7),
    ("Ciudad Córdoba", 15, 3.404, -76.492, 2, 2.6),
    ("Mariano Ramos", 16, 3.409, -76.505, 2, 2.8),
    ("Compartir", 21, 3.420, -76.474, 2, 2.5),
    ("Ñ", 18, 3.392, -76.551, 3, 3.4),  # nombre de una letra (caso extremo)
]

AMEN_CATS = {
    "mio": ["Estación Universidades", "Estación Unidad Deportiva", "Estación Tequendama", "Terminal Menga", "Estación Chiminangos", "Estación Plaza de Caycedo", "Terminal Cañaveralejo"],
    "mio_stop": ["Parada Cra 66 con Cl 5", "Parada Av Pasoancho", "Parada Cl 70 Norte", "Parada Cra 100"],
    "mall": ["Unicentro", "Jardín Plaza", "Chipichape", "Palmetto Plaza", "Cosmocentro", "Pacific Mall", "Centenario", "Premier Limonar", "Único Outlet"],
    "super": ["Éxito", "Carulla", "Jumbo", "Olímpica", "D1", "Ara", "Mercamío"],
    "park": ["Parque del Perro", "Parque de la Caña", "Parque del Ingenio", "Ecoparque Lago de las Garzas", "Parque Panamericano", "Bulevar del Río"],
    "health": ["Fundación Valle del Lili", "Clínica Imbanaco", "Clínica de Occidente", "Clínica Farallones", "Hospital Universitario del Valle", "Clínica Versalles"],
    "edu": ["Universidad del Valle", "Universidad Javeriana", "Universidad Icesi", "Colegio Bolívar", "Colegio Berchmans", "IE Santa Librada"],
    "food": ["Restaurante El Zaguán", "Café Macondo", "Ringlete", "Platillos Voladores", "Antigua Contemporánea"],
    "bank": ["Bancolombia", "Banco de Occidente", "Drogas La Rebaja", "Cruz Verde", "Davivienda"],
    "gym": ["Bodytech", "Smart Fit", "Unidad Deportiva Panamericana", "Spinning Center"],
}

PEN = {
    "inund_nm": (30, "Zona de amenaza no mitigable por inundación (POT 2014)"),
    "inund": (12, "Zona inundable: río Cauca o pluvial"),
    "masa": (15, "Ladera con amenaza de remoción en masa"),
    "homic": (15, "{n} homicidios a 500 m en 24 meses (P90 de la ciudad)"),
    "homic75": (7, "{n} homicidios a 500 m en 24 meses (P75 de la ciudad)"),
    "mejora": (10, "Entorno de mejoramiento integral"),
    "ruido": (5, "Ruido nocturno > {db} dB"),
    "legal": (10, "Posible tema legal: «{kw}»"),
    "aprox": (0, "Ubicación aproximada: el análisis de zona usa el barrio"),
}
LEGAL_KW = ["venta de derechos", "proindiviso", "sucesión en trámite", "sin escritura", "remate", "cesión de derechos"]
PROJECTS = [("renov", "Renovación urbana R2"), ("plan_parcial", "Plan parcial Ciudad Paraíso"), ("mio_proy", "Troncal MIO proyectada Cl 5 sur"),
            ("corredor_verde", "Corredor Verde"), ("tren", "Tren de cercanías Cali–Jamundí"), ("plusvalia", "Área generadora de plusvalía")]

FEATS = ["balcón", "ascensor", "piscina", "gimnasio", "portería 24 h", "zona BBQ", "cuarto útil", "estudio", "patio", "terraza", "parque infantil"]


N_IMG = 16


def make_images(r):
    """Fotos falsas locales (SVG) para no depender de servicios externos."""
    d = ROOT / "dev" / "img"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(N_IMG):
        hue = r.randint(0, 360)
        wall = f"hsl({hue},18%,{r.randint(62, 82)}%)"
        floor = f"hsl({(hue + 30) % 360},25%,{r.randint(30, 50)}%)"
        win = f"hsl({r.randint(190, 215)},55%,{r.randint(70, 85)}%)"
        wx, ww = r.randint(60, 420), r.randint(160, 260)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 560">'
               f'<rect width="800" height="560" fill="{wall}"/>'
               f'<rect x="{wx}" y="90" width="{ww}" height="200" fill="{win}" stroke="#fff" stroke-width="10"/>'
               f'<line x1="{wx + ww // 2}" y1="90" x2="{wx + ww // 2}" y2="290" stroke="#fff" stroke-width="8"/>'
               f'<polygon points="0,560 800,560 800,380 0,420" fill="{floor}"/>'
               f'<rect x="{r.randint(420, 600)}" y="330" width="150" height="70" rx="10" fill="hsl({hue},20%,30%)"/>'
               f'<text x="24" y="540" font-family="sans-serif" font-size="22" fill="rgba(0,0,0,.35)">foto de prueba {i}</text></svg>')
        (d / f"p{i}.svg").write_text(svg, encoding="utf-8")


def _round(c, nd=4):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    out = []
    for x in c:
        y = _round(x, nd)
        if isinstance(y[0], (int, float)) and out and out[-1] == y:
            continue
        out.append(y)
    return out


def load_barrios():
    """data/layers/barrios.geojson -> {code: {name, comuna, c: (lat, lng), geometry}} (simplificado)."""
    src = ROOT.parent / "data" / "layers" / "barrios.geojson"
    d = json.loads(src.read_text(encoding="utf-8"))
    out = {}
    for f in d["features"]:
        p, g = f["properties"], f["geometry"]
        ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        lng = sum(x[0] for x in ring) / len(ring)
        lat = sum(x[1] for x in ring) / len(ring)
        geom = {"type": g["type"], "coordinates": _round(g["coordinates"])}
        out[str(p["barcodigo"])] = {"name": p["barnombre"], "comuna": p["barcomuna"], "c": (lat, lng), "geometry": geom}
    return out


def write_barrios(real, codes):
    (OUT).mkdir(parents=True, exist_ok=True)
    feats = [{"type": "Feature", "properties": {"code": c, "name": b["name"], "comuna": int(b["comuna"])}, "geometry": b["geometry"]}
             for c, b in real.items()]
    (OUT / "barrios.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def md5_2(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:2]


def jitter(r, sd):
    return r.gauss(0, sd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--with-errors", action="store_true", help="la última corrida de m2 trae errores (prueba del aviso)")
    args = ap.parse_args()
    r = random.Random(args.seed)
    now = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
    today = now.date()

    make_images(r)
    codes = {}
    zones = {}
    # barrios reales (IDESC): cada barrio falso toma el polígono real más cercano
    real = load_barrios()
    used = set()
    for i, (name, cm, lat, lng, e, base) in enumerate(BARRIOS):
        code = min((c for c in real if c not in used), key=lambda c: (real[c]["c"][0] - lat) ** 2 + (real[c]["c"][1] - lng) ** 2)
        used.add(code)
        real[code]["name"] = name
        real[code]["comuna"] = int(real[code]["comuna"])
        BARRIOS[i] = (name, real[code]["comuna"], lat, lng, e, base)
        codes[name] = code
        zones[code] = {"name": name, "comuna": real[code]["comuna"], "n_sale": 0, "median_m2": int(base * 1e6),
                       "trend_12m": round(r.uniform(-0.03, 0.12), 3), "dom": r.randint(25, 140),
                       "yield": round(r.uniform(0.045, 0.075), 3), "n_rent": r.randint(5, 200)}
    # el resto de barrios: estadísticas sintéticas (algunos sin datos, a propósito)
    for code, b in real.items():
        if code in zones:
            continue
        n = r.choice([0, 1, 4, 6, 8, 12, 15, 25, 40, 5])
        has = n >= 3
        zones[code] = {"name": b["name"], "comuna": int(b["comuna"]), "n_sale": n,
                       "median_m2": int(r.uniform(2.2, 6.5) * 1e6) if has else None,
                       "trend_12m": round(r.uniform(-0.05, 0.14), 3) if has and r.random() > 0.15 else None,
                       "dom": r.randint(20, 160) if has else None,
                       "yield": round(r.uniform(0.04, 0.08), 3) if has and r.random() > 0.2 else None,
                       "n_rent": r.randint(0, 80) if has else 0}
    write_barrios(real, codes)

    listings, details = [], {}
    for n in range(args.n):
        b = r.choices(BARRIOS, weights=[3 if x[4] >= 4 else 2 for x in BARRIOS])[0]
        name, cm, blat, blng, e, base = b
        src = r.choice(["fr", "m2"])
        lid = f"{src}-{100000 + n * 7 + r.randint(0, 6)}"
        t = "a" if r.random() < 0.72 else "c"
        a = round(max(28, r.lognormvariate(math.log(85 if t == "a" else 160), 0.38)), 1)
        a = min(a, 480.0)
        pm_base = base * 1e6 * (1.0 if t == "a" else 0.92)
        pm = pm_base * math.exp(jitter(r, 0.17))
        pm = min(max(pm, 2.5e6), 9e6)
        p = int(round(pm * a / 1e6)) * 1_000_000
        p = min(max(p, 120_000_000), 2_500_000_000)
        pm = p / a
        missing_area = r.random() < 0.035
        bedrooms = max(1, min(7, int(round(a / 32 + jitter(r, 0.6)))))
        baths = max(1, min(6, int(round(bedrooms * 0.75 + jitter(r, 0.5)))))
        pk = r.choice([0, 0, 1, 1, 1, 2, 2, 3]) if e >= 4 else r.choice([0, 0, 0, 1, 1])
        age = r.choice([None, 2, 5, 8, 15, 15, 25, 25, 35])
        lat = blat + jitter(r, 0.0045)
        lng = blng + jitter(r, 0.0045)
        lat, lng = min(max(lat, 3.36), 3.49), min(max(lng, -76.56), -76.47)
        gp = "e" if r.random() < 0.85 else "b"

        expected = pm_base * math.exp(0.12 + jitter(r, 0.05))
        if n == 5:
            expected = pm * 1.25
        d = 1 - pm / expected
        arv = expected * r.uniform(1.06, 1.3)
        up = arv / pm - 1

        remodel = (age or 0) >= 25 and r.random() < 0.6 or r.random() < 0.06
        legal_kw = r.choice(LEGAL_KW) if r.random() < 0.05 else None
        suspicious = r.random() < 0.012
        if suspicious:  # arriendo publicado como venta: precio irreal
            p = r.choice([1_800_000, 2_500_000, 950_000])
            pm = p / a
            d = None
            up = None

        pens = []
        def addp(k, **kw):
            pts, why = PEN[k]
            pens.append({"k": k, "pts": pts, "why": why.format(**kw)})
        if cm in (6, 7, 13, 21) and r.random() < 0.35:
            addp("inund_nm" if r.random() < 0.25 else "inund")
        if cm in (18, 19, 3) and r.random() < 0.12:
            addp("masa")
        if cm in (13, 15, 16, 21, 6, 7) and r.random() < 0.3:
            addp("homic", n=r.randint(9, 20)) if r.random() < 0.5 else addp("homic75", n=r.randint(5, 8))
        if cm in (13, 15, 21) and r.random() < 0.2:
            addp("mejora")
        if r.random() < 0.06:
            addp("ruido", db=r.choice([65, 68, 70, 72]))
        if legal_kw:
            addp("legal", kw=legal_kw)
        if gp == "b":
            addp("aprox")
        if n == 5:  # caso extremo: todas las penalizaciones
            pens = []
            for k in ("inund_nm", "masa", "homic", "mejora", "ruido", "legal", "aprox"):
                addp(k, n=18, db=72, kw="sucesión en trámite")
            legal_kw = "sucesión en trámite"

        c1 = max(0, min(100, 50 + (d or 0) * 260 + jitter(r, 6)))
        c2 = 0 if (up or 0) <= 0 else max(0, min(100, (up or 0) * 300 + jitter(r, 8)))
        c3 = max(0, min(100, 50 + zones[codes[name]]["trend_12m"] * 250 + jitter(r, 12)))
        c4 = max(0, min(100, e * 14 + jitter(r, 14)))
        c5 = max(0, min(100, 100 - zones[codes[name]]["dom"] * 0.5 + jitter(r, 8)))
        comps_k = r.randint(4, 20)
        conf = min(1, comps_k / 12) * (1 if gp == "e" else 0.7) * (0 if missing_area else 1)
        base_s = 0.35 * c1 + 0.25 * c2 + 0.15 * c3 + 0.15 * c4 + 0.10 * c5
        s = max(0, min(100, base_s - sum(x["pts"] for x in pens))) * (0.6 + 0.4 * conf)
        rankable = not missing_area and not suspicious and d is not None and d > 0 and a > 20
        if not rankable:
            s = None

        fs = today - timedelta(days=r.choice([0, 0, 1, 1, 2, 3, 5, 9, 14, 21, 30, 45, 60, 90, 120]))
        dom = (today - fs).days + r.randint(0, 40)
        hist = [{"d": fs.isoformat(), "p": p}]
        pd = None
        if r.random() < 0.18 and not suspicious:
            fs = min(fs, today - timedelta(days=r.randint(14, 60)))
            first = int(p * r.uniform(1.03, 1.18) / 1e6) * 1_000_000
            mid = int((first + p) / 2e6) * 1_000_000
            hist = [{"d": fs.isoformat(), "p": first}]
            if r.random() < 0.4:
                hist.append({"d": (fs + timedelta(days=10)).isoformat(), "p": mid})
            hist.append({"d": (fs + timedelta(days=r.randint(11, (today - fs).days))).isoformat(), "p": p})
            hist.sort(key=lambda h: h["d"])
            pd = round(p / first - 1, 3)
        tags = []
        if (today - fs).days < 2:
            tags.append("nueva")
        if pd is not None and pd < 0:
            tags.append("rebaja")
        if remodel:
            tags.append("remodelar")
        if legal_kw:
            tags.append("legal")
        if suspicious:
            tags.append("sospechoso")

        no_photos = r.random() < 0.05
        nph = 0 if no_photos else r.randint(1, 14)
        ph0 = r.randrange(N_IMG)
        photos = [f"dev/img/p{(ph0 + k) % N_IMG}.svg" for k in range(nph)]
        srcs = [src] if r.random() < 0.8 else ["fr", "m2"]

        listings.append({
            "id": lid, "t": t, "p": p,
            "a": None if missing_area else a,
            "pm": None if missing_area else int(pm),
            "b": bedrooms if r.random() > 0.02 else None,
            "ba": baths if r.random() > 0.02 else None,
            "pk": pk, "e": e, "age": age,
            "br": name, "brc": codes[name], "cm": cm,
            "lat": round(lat, 6), "lng": round(lng, 6), "gp": gp,
            "s": None if s is None else round(s, 1),
            "c": [round(c1), round(c2), round(c3), round(c4), round(c5)],
            "d": None if d is None else round(d, 3),
            "up": None if up is None else round(up, 3),
            "conf": round(conf, 2),
            "tags": tags, "pen": pens,
            "img": photos[0] if nph else None,
            "fs": fs.isoformat(), "pd": pd, "src": srcs, "dom": dom,
        })
        zones[codes[name]]["n_sale"] += 1

        tipo = "Apartamento" if t == "a" else "Casa"
        desc_parts = [f"{tipo} en venta en {name}, Cali. {a:g} m² construidos, {bedrooms} habitaciones, {baths} baños."]
        desc_parts.append("Cuenta con " + ", ".join(r.sample(FEATS, 3)) + ".")
        if remodel:
            desc_parts.append("Inmueble original, para remodelar a su gusto.")
        if legal_kw:
            desc_parts.append(f"Se vende por {legal_kw}; documentos al día con abogado.")
        if n % 97 == 0:
            desc_parts.append(("Excelente ubicación, cerca a centros comerciales, vías principales y transporte. " * 8).strip())
        comps = []
        for k in range(comps_k):
            cpm = int(expected * math.exp(jitter(r, 0.1)))
            ca = round(a * r.uniform(0.75, 1.25), 1)
            ang, dist = r.uniform(0, 2 * math.pi), r.uniform(60, 800 if k < 10 else 1500)
            comps.append({"id": f"{r.choice(['fr', 'm2'])}-{r.randint(200000, 999999)}",
                          "lat": round(lat + dist * math.sin(ang) / 111_000, 6),
                          "lng": round(lng + dist * math.cos(ang) / 111_000, 6),
                          "pm": cpm, "a": ca, "dist": int(dist), "url": "https://www.fincaraiz.com.co/"})
        comps.sort(key=lambda c: c["dist"])
        med = sorted(c["pm"] for c in comps)[len(comps) // 2]
        maxd = max(c["dist"] for c in comps)
        amen = []
        for cat, names in AMEN_CATS.items():
            if r.random() < 0.1:
                continue
            for nm in r.sample(names, min(3, len(names))):
                amen.append({"cat": cat, "name": nm, "dist": r.randint(80, 2600)})
        risks = [{"k": x["k"], "why": x["why"]} for x in pens if x["k"] != "legal"]
        projects = [{"k": k, "name": nm, "dist": r.randint(100, 1500)} for k, nm in r.sample(PROJECTS, r.choice([0, 0, 1, 2]))]
        urls = []
        for s_ in srcs:
            urls.append(f"https://www.fincaraiz.com.co/inmueble/{lid}" if s_ == "fr" else f"https://www.metrocuadrado.com/inmueble/venta/{lid}")
        z = zones[codes[name]]
        details[lid] = {
            "title": f"{tipo} en venta, {name}" + (" — para remodelar" if remodel else ""),
            "desc": " ".join(desc_parts),
            "photos": photos, "urls": urls,
            "admin": int(r.uniform(1.5, 7) * a * 1000 / 1000) * 1000 if t == "a" else None,
            "floor": r.randint(1, 22) if t == "a" else None,
            "area_lot": round(a * r.uniform(1.0, 1.6), 1) if t == "c" else None,
            "history": hist,
            "expected_m2": int(expected), "arv_m2": int(arv),
            "max_offer_70": None if missing_area else int(arv * a * 0.7),
            "comps": comps,
            "comps_info": f"{len(comps)} comparables a ≤ {'800 m' if maxd <= 800 else '1,5 km'}, mediana ${med / 1e6:.1f}M/m²".replace(".", ","),
            "amen": amen, "risks": risks, "projects": projects,
            "zone": {"median_m2": z["median_m2"], "trend_12m": z["trend_12m"], "dom": z["dom"], "yield": z["yield"]},
        }

    if OUT.exists():
        for f in ("listings.json", "zones.json", "meta.json"):
            (OUT / f).unlink(missing_ok=True)
        shutil.rmtree(OUT / "detail", ignore_errors=True)
    (OUT / "detail").mkdir(parents=True, exist_ok=True)
    gen = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    dump = lambda path, obj: path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dump(OUT / "listings.json", {"generated_at": gen, "listings": listings})
    shards = {}
    for lid, det in details.items():
        shards.setdefault(md5_2(lid), {})[lid] = det
    for k, v in shards.items():
        dump(OUT / "detail" / f"{k}.json", v)
    dump(OUT / "zones.json", zones)
    runs = []
    for i in range(10):
        at = now - timedelta(hours=6 * i)
        for src in ("fr", "m2"):
            fetched = r.randint(900, 2600)
            errs = 1 if (i == 3 and src == "fr") else 0  # error viejo: no activa el aviso
            if args.with_errors and i == 0 and src == "m2":
                errs = 4
            runs.append({"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": src, "mode": "full" if i % 4 == 0 else "incremental",
                         "fetched": fetched, "new": r.randint(5, 120), "removed": r.randint(0, 40), "errors": errs, "ok": errs == 0})
    dump(OUT / "meta.json", {"generated_at": gen, "fixture": True,
                              "weights": {"c1_discount": 0.35, "c2_upside": 0.25, "c3_zone": 0.15, "c4_amenities": 0.15, "c5_liquidity": 0.10},
                              "runs": runs, "layers": {"osm": "2026-11-10", "idesc": "2026-11-01", "mio_gtfs": "2026-11-01", "barrios": "2026-11-14", "inundacion_fluvial": "2026-11-14"},
                              "counts": {"sale_active": len(listings), "ranked": sum(1 for x in listings if x["s"] is not None)}})
    size = (OUT / "listings.json").stat().st_size
    print(f"ok: {len(listings)} ofertas, {len(shards)} shards de detalle, listings.json {size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()

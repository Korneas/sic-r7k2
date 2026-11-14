#!/usr/bin/env python3
"""Download static geo layers into data/layers/ (GeoJSON, EPSG:4326, simplified).

Usage:
  python scripts/fetch_layers.py                # fetch everything missing/older than 30 days
  python scripts/fetch_layers.py --force        # refresh everything
  python scripts/fetch_layers.py --only osm_pois [--only barrios ...]
  python scripts/fetch_layers.py --list
Writes data/layers/manifest.json {layer: {fetched, features, source}} and
config/anchors.yaml (big malls, coordinates matched from the OSM download).
"""
import argparse, collections, csv, datetime as dt, io, json, math, re, sys, tempfile, time, unicodedata, zipfile
from pathlib import Path

import httpx
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "layers"
MANIFEST = OUT / "manifest.json"
ANCHORS = ROOT / "config" / "anchors.yaml"
WFS = "https://ws-idesc.cali.gov.co/geoserver/wfs"
UA = {"User-Agent": "sic-research/0.1"}
TOL = 0.00005
TODAY = dt.date.today()
MIN_HOM_YEAR = TODAY.year - 3
STALE_DAYS = 30
BBOX = (3.33, -76.60, 3.51, -76.45)  # S, W, N, E
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
GTFS_URLS = [
    "https://files.mobilitydatabase.org/mdb-3356/mdb-3356-202607130343/mdb-3356-202607130343.zip",
    "https://github.com/ColombiaInfo/ColombiaGTFS/raw/master/Cali%20-%20Mio/GTFS-MIO.zip",
]

client = httpx.Client(headers=UA, timeout=httpx.Timeout(600, connect=30), follow_redirects=True)


def log(*a):
    print(*a, flush=True)


def get(url, params=None, retries=4):
    err = None
    for i in range(retries):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa
            err = e
            time.sleep(3 * (i + 1))
    raise err


# ---------------------------------------------------------------- geometry utils
def rnd(c):
    if isinstance(c[0], (int, float)):
        return [round(c[0], 6), round(c[1], 6)]
    return [rnd(x) for x in c]


def clean_geom(g, simplify=True):
    s = shape(g)
    if s.is_empty:
        return None
    if simplify and s.geom_type != "Point" and s.geom_type != "MultiPoint":
        s = s.simplify(TOL, preserve_topology=True)
        if s.is_empty:
            return None
    m = mapping(s)
    return {"type": m["type"], "coordinates": rnd(m["coordinates"])}


def write_fc(name, feats):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))
    return p


def wfs_features(layer, props=None):
    params = dict(service="WFS", version="1.0.0", request="GetFeature", outputFormat="application/json",
                  srsName="EPSG:4326", typeName=layer)
    if props:
        params["propertyName"] = ",".join(props)
    return get(WFS, params).json()["features"]


def wfs_url(layer):
    return f"{WFS}?service=WFS&request=GetFeature&typeName={layer}"


# ---------------------------------------------------------------- generic WFS layers
# name: (typeName, kept properties or None=all, extra)
WFS_LAYERS = {
    "barrios": ("dapm:pdt_dpa_barrios_sectores", ["barcodigo", "barnombre", "barcomuna"], {}),
    "comunas": ("dapm:pdt_dpa_comunas", ["comcodigo", "comnombre"], {}),
    "inundacion_fluvial": ("pot_2014:amb_ari_inundacion_fluvial", ["tipo_amena"], {}),
    "inundacion_pluvial": ("pot_2014:amb_ari_inundacion_pluvial", ["tipo_amena"], {}),
    "inundacion_no_mitigable": ("expediente_municipal:emc_amb_ari_amenaza_no_mitigable_inundacion", ["tipo_amena", "amenaza_in"], {}),
    "movimientos_masa": ("pot_2014:amb_ari_movimientos_masa", None, {}),
    # slope % (5 m raster polygonised); only >= 25% kept, as ladera proxy while movimientos_masa is down
    "pendientes": ("pot_2014:bcs_pendientes_porcentaje", ["pend_porc", "id_pend"],
                   {"keep": lambda p: int(p["id_pend"] or 0) >= 5, "dissolve": "pend_porc", "prune": (4e-6, 0.00012)}),
    "tratamientos": ("pot_2014:nur_tratamientos_urbanisticos", ["codigo", "tratamient"], {}),
    "plusvalia": ("pot_2014:nur_areas_generadoras_plusvalia", ["nombre", "tipo"], {}),
    "planes_parciales": ("emru:emru_pcp_planes_parciales", ["plan_parci"], {"dissolve": "plan_parci"}),
    "ciudad_paraiso": ("emru:emru_pcp_ciudad_paraiso", ["plan_parci"], {"dissolve": "plan_parci"}),
    "troncales_proyectadas": ("pot_2014:mov_sitm_troncales_proyectadas", ["tipo", "nombre"], {}),
    "corredor_ferreo": ("pot_2014:mov_corredor_ferreo", ["tramo"], {}),
    "ips": ("salud:ads_ips_capacidad_reps", None, {"post": "ips"}),
    "colegios": ("educacion:esf_establecimiento_educativo", {"eenomsede": "name", "eenomins": "institucion", "eeestado": "estado", "ieomatrtot": "matricula"}, {}),
    "recreacion": ("pot_2014:eqp_uco_recreacion", ["nombre", "tipo", "propiedad", "escala"], {}),
    "escenarios_deportivos": ("deporte_recreacion:escenarios_deportivos", ["nombre"], {}),
    "barrios_precarios": ("vivienda:pmih_barrios_precarios", ["bpcodigo", "bpnombre"], {}),
    "mejoramiento": ("pot_2014:nur_programa_mejoramiento", ["programa"], {}),
    "homicidios": ("seguridad_justicia:ps_os_homicidios", {"homfechah": "date", "homanioh": "year"}, {"post": "hom", "simplify": False}),
    "ciclorutas": ("pot_2014:mov_jerarquizacion_ciclorutas", ["condicion", "prioridad", "nombre"], {}),
    "ruido_noche": ("dagma:obs_rui_semana_noche", {"gridcode": "db"}, {}),
}


def pick(props, spec):
    if spec is None:
        return {k: v for k, v in props.items() if not (isinstance(v, str) and len(v) > 120)}
    if isinstance(spec, dict):
        return {new: props.get(old) for old, new in spec.items()}
    return {k: props.get(k) for k in spec}


def fetch_wfs_layer(name):
    typename, spec, extra = WFS_LAYERS[name]
    feats = wfs_features(typename)
    out = []
    if extra.get("post") == "ips":
        sedes = {}
        for f in feats:
            if not f["geometry"]:
                continue
            p = f["properties"]
            s = sedes.setdefault(p["cod_hab"], {"geometry": f["geometry"], "name": p["nombre_ips"], "natu_ips": p["natu_ips"],
                                                "cla_ips": p["cla_ips"], "beds": 0, "has_salas": False})
            if p["nom_capa_reps"] == "CAMAS":
                s["beds"] += int(p["cantidad"] or 0)
            if p["nom_capa_reps"] == "SALAS":
                s["has_salas"] = True
        for k, s in sedes.items():
            g = clean_geom(s.pop("geometry"), False)
            s.update(cod_hab=k, has_beds=s["beds"] > 0)
            out.append({"type": "Feature", "geometry": g, "properties": s})
        return out
    groups = collections.defaultdict(list)
    for f in feats:
        if not f["geometry"]:
            continue
        p = pick(f["properties"], spec)
        if "keep" in extra and not extra["keep"](p):
            continue
        if extra.get("post") == "hom":
            try:
                if int(p["year"]) < MIN_HOM_YEAR:
                    continue
            except (TypeError, ValueError):
                continue
            p["date"] = (p["date"] or "")[:10]
        if "dissolve" in extra:
            groups[p[extra["dissolve"]]].append(shape(f["geometry"]).buffer(0))
            continue
        g = clean_geom(f["geometry"], extra.get("simplify", True))
        if g:
            out.append({"type": "Feature", "geometry": g, "properties": p})
    for key, geoms in groups.items():
        u = unary_union(geoms)
        if "prune" in extra:
            u = prune(u, *extra["prune"])
        g = clean_geom(mapping(u), True)
        if g:
            out.append({"type": "Feature", "geometry": g, "properties": {extra["dissolve"]: key}})
    return out


def prune(g, min_area, tol):
    """Drop polygon parts and holes smaller than min_area (deg²), then simplify (raster-derived layers)."""
    out = []
    for q in getattr(g, "geoms", [g]):
        if q.geom_type == "Polygon" and q.area >= min_area:
            out.append(Polygon(q.exterior, [h for h in q.interiors if Polygon(h).area >= min_area]))
    return MultiPolygon(out).buffer(0).simplify(tol, preserve_topology=True)


# ---------------------------------------------------------------- estrato
def fetch_estrato():
    """Join key: predio estidterre[:8] (= barrio4 + manzana4) == manzanas comuna2 + barrio2 + manzana4."""
    per_mz = collections.defaultdict(collections.Counter)
    per_bar = collections.defaultdict(collections.Counter)
    base = dict(service="WFS", version="2.0.0", request="GetFeature", typeNames="dapm:pdt_est_estrato_urbano_expansion",
                propertyName="estidterre,estidestra,estidusopr,estidbar", sortBy="estidpredi", outputFormat="csv", count=50000)
    start, total = 0, 0
    while True:
        txt = get(WFS, {**base, "startIndex": start}).text
        rows = list(csv.DictReader(io.StringIO(txt)))
        for r in rows:
            e = r["estidestra"]
            if r["estidusopr"] != "A" or e not in "123456" or not e:  # residential only
                continue
            per_mz[r["estidterre"][:8]][int(e)] += 1
            per_bar[r["estidbar"]][int(e)] += 1
        total += len(rows)
        log(f"  estrato predios: {total}")
        if len(rows) < 50000:
            break
        start += 50000
    (OUT / "estrato_barrios.json").write_text(json.dumps(
        {b: {"estrato": c.most_common(1)[0][0], "n": sum(c.values())} for b, c in sorted(per_bar.items())}, separators=(",", ":")))
    mz = wfs_features("catastro:cat_bas_manzanas", ["the_geom", "comuna", "barrio", "manzana"])
    feats, matched = [], 0
    for f in mz:
        p = f["properties"]
        key = f"{p['comuna']}{p['barrio']}{p['manzana']}"
        if not f["geometry"]:
            continue
        c = per_mz.get(key)
        if not c:
            continue
        matched += 1
        g = clean_geom(f["geometry"])
        if g:
            feats.append({"type": "Feature", "geometry": g,
                          "properties": {"key": key, "estrato": c.most_common(1)[0][0], "n": sum(c.values())}})
    log(f"  manzanas: {len(mz)} total, {matched} with residential estrato ({100*matched/len(mz):.1f}%); "
        f"manzana keys in predios: {len(per_mz)}")
    return feats


# ---------------------------------------------------------------- OSM
CATS = [  # (cat, overpass tag filter, tag key for sub)
    ("mall", '["shop"="mall"]'), ("supermarket", '["shop"="supermarket"]'),
    ("hospital", '["amenity"~"^(hospital|clinic)$"]'), ("school", '["amenity"~"^(school|university|college)$"]'),
    ("restaurant", '["amenity"~"^(restaurant|cafe)$"]'), ("bank", '["amenity"="bank"]'), ("pharmacy", '["amenity"="pharmacy"]'),
    ("fitness", '["leisure"~"^(fitness_centre|sports_centre)$"]'), ("station", '["public_transport"="station"]'),
    ("bus_stop", '["highway"="bus_stop"]'),
]
M_LAT, M_LNG = 110574.0, 111320.0 * math.cos(math.radians(3.43))


def overpass(query):
    err = None
    for i in range(6):
        url = OVERPASS[i % len(OVERPASS)]
        try:
            r = client.post(url, data={"data": query})
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()["elements"]
            err = f"{url} -> {r.status_code}"
        except Exception as e:  # noqa
            err = f"{url} -> {e}"
        log("  overpass retry:", err)
        time.sleep(15 * (i + 1))
    raise RuntimeError(err)


def classify(tags):
    for cat, flt in CATS:
        m = re.findall(r'"([^"]+)"(=|~)"([^"]+)"', flt)[0]
        k, op, v = m
        val = tags.get(k)
        if val is not None and (val == v if op == "=" else re.fullmatch(v, val)):
            return cat
    return None


def area_m2(el):
    lines = []
    if el["type"] == "way" and "geometry" in el:
        lines = [[(p["lon"], p["lat"]) for p in el["geometry"]]]
    elif el["type"] == "relation":
        for m in el.get("members", []):
            if m.get("role") == "outer" and m.get("geometry"):
                lines.append([(p["lon"], p["lat"]) for p in m["geometry"]])
    from shapely.geometry import LineString
    try:
        if el["type"] == "way":
            ring = lines[0]
            if len(ring) < 4 or ring[0] != ring[-1]:
                return None
            polys = [shape({"type": "Polygon", "coordinates": [ring]}).buffer(0)]
        else:
            polys = list(polygonize([LineString(l) for l in lines if len(l) > 1]))
    except Exception:
        return None
    if not polys:
        return None
    a = sum(p.area for p in polys) * M_LAT * M_LNG
    return round(a)


def fetch_osm():
    bb = ",".join(map(str, BBOX))
    parts = "".join(f"nwr{flt}({bb});" for cat, flt in CATS)
    els = overpass(f"[out:json][timeout:240];({parts});out center tags;")
    park = overpass(f'[out:json][timeout:240];(nwr["leisure"="park"]({bb}););out geom tags;')
    feats = []
    for el in els:
        tags = el.get("tags", {})
        cat = classify(tags)
        if not cat:
            continue
        if el["type"] == "node":
            lat, lng = el["lat"], el["lon"]
        else:
            c = el.get("center")
            if not c:
                continue
            lat, lng = c["lat"], c["lon"]
        props = {"cat": cat, "name": tags.get("name")}
        if cat == "hospital":
            props["sub"] = tags.get("amenity")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
                      "properties": props})
    for el in park:
        tags = el.get("tags", {})
        if el["type"] == "node":
            lat, lng, a = el["lat"], el["lon"], None
        else:
            pts = el.get("geometry") or [p for m in el.get("members", []) for p in (m.get("geometry") or [])]
            if not pts:
                continue
            b = el.get("bounds")
            lat = (b["minlat"] + b["maxlat"]) / 2 if b else sum(p["lat"] for p in pts) / len(pts)
            lng = (b["minlon"] + b["maxlon"]) / 2 if b else sum(p["lon"] for p in pts) / len(pts)
            a = area_m2(el)
        props = {"cat": "park", "name": tags.get("name")}
        if a:
            props["area_m2"] = a
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
                      "properties": props})
    return feats


# ---------------------------------------------------------------- MIO GTFS
def fetch_mio():
    err = None
    for url in GTFS_URLS:
        try:
            with tempfile.TemporaryFile() as tmp:
                with client.stream("GET", url) as r:
                    r.raise_for_status()
                    for chunk in r.iter_bytes():
                        tmp.write(chunk)
                tmp.seek(0)
                with zipfile.ZipFile(tmp) as z:
                    txt = z.read("stops.txt").decode("utf-8-sig")
            feats = []
            for r in csv.DictReader(io.StringIO(txt)):
                try:
                    lat, lng = float(r["stop_lat"]), float(r["stop_lon"])
                except ValueError:
                    continue
                nm = r["stop_name"]
                station = r.get("location_type") == "1" or re.search(r"estaci[oó]n|terminal", nm, re.I)
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
                              "properties": {"name": nm, "kind": "station" if station else "stop", "stop_id": r["stop_id"]}})
            return feats, url
        except Exception as e:  # noqa
            err = e
            log("  gtfs failed:", url, e)
    raise err


# ---------------------------------------------------------------- anchors
MALLS = [  # (display name, regexes on normalised OSM name)
    ("Chipichape", r"chipichape"), ("Unicentro", r"unicentro"), ("Jardín Plaza", r"jard[ií]n plaza"),
    ("Palmetto Plaza", r"palmetto"), ("Cosmocentro", r"cosmocentro"), ("Pacific Mall", r"pacific mall"),
    ("Centenario", r"centro comercial centenario"), ("Premier El Limonar", r"limonar"), ("Único Outlet", r"[uú]nico"),
    ("Holguines Trade Center", r"holguines"), ("Calima", r"la 14 de calima|centro comercial calima"), ("La Estación", r"centro comercial la estaci[oó]n"),
    ("Mallplaza Cali", r"mall ?plaza"), ("Aventura Plaza", r"aventura"), ("Multicentro", r"multicentro"),
    ("Río Cauca", r"centro comercial r[ií]o cauca"),
]


def norm(s):
    return unicodedata.normalize("NFC", s or "").lower()


def write_anchors():
    p = OUT / "osm_pois.geojson"
    if not p.exists():
        log("anchors: osm_pois.geojson missing, skipped")
        return
    pois = [f for f in json.loads(p.read_text())["features"] if f["properties"]["cat"] == "mall" and f["properties"].get("name")]
    names = [f for f in pois]
    need = [d for d, rx in MALLS if not any(re.search(rx, norm(f["properties"]["name"])) for f in pois)]
    if need:  # many big malls are not tagged shop=mall in OSM: search by name (any tag)
        rx = "|".join(r for d, r in MALLS if d in need)
        bb = ",".join(map(str, BBOX))
        try:
            for el in overpass(f'[out:json][timeout:90];nwr["name"~"{rx}",i]({bb});out center tags;'):
                c = el.get("center") or el
                pois.append({"geometry": {"coordinates": [c["lon"], c["lat"]]}, "properties": {"name": el["tags"]["name"]}})
        except Exception as e:  # noqa
            log("anchors: name search failed", e)
    lines = ["# Major Cali malls (weight x2 in amenities). Coordinates matched by name from OSM (scripts/fetch_layers.py).", "malls:"]
    missing = []
    for disp, rx in MALLS:
        cands = [f for f in pois if re.search(rx, norm(f["properties"]["name"]))]
        if not cands:
            missing.append(disp)
            continue
        f = cands[0]
        lng, lat = f["geometry"]["coordinates"]
        lines += [f"  - name: \"{disp}\"", f"    osm_name: \"{f['properties']['name']}\"", f"    lat: {lat}", f"    lng: {lng}"]
    if missing:
        lines.append("# not found in OSM: " + ", ".join(missing))
    ANCHORS.parent.mkdir(exist_ok=True)
    ANCHORS.write_text("\n".join(lines) + "\n")
    log(f"anchors: {len(MALLS)-len(missing)} written; missing: {missing}")


# ---------------------------------------------------------------- driver
def layer_names():
    return list(WFS_LAYERS) + ["estrato_manzanas", "osm_pois", "mio_stops"]


def run(name):
    if name in WFS_LAYERS:
        return fetch_wfs_layer(name), wfs_url(WFS_LAYERS[name][0])
    if name == "estrato_manzanas":
        return fetch_estrato(), wfs_url("dapm:pdt_est_estrato_urbano_expansion") + " + catastro:cat_bas_manzanas"
    if name == "osm_pois":
        return fetch_osm(), OVERPASS[0]
    if name == "mio_stops":
        return fetch_mio()
    raise KeyError(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="layer name (repeatable)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print("\n".join(layer_names()))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    man = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    names = a.only or layer_names()
    bad = [n for n in names if n not in layer_names()]
    if bad:
        sys.exit(f"unknown layer(s): {bad}; see --list")
    failed = []
    for n in names:
        m = man.get(n)
        fresh = m and (OUT / f"{n}.geojson").exists() and (TODAY - dt.date.fromisoformat(m["fetched"])).days < STALE_DAYS
        if fresh and not a.only and not a.force:
            log(f"[skip] {n} (fetched {m['fetched']})")
            continue
        log(f"[fetch] {n}")
        try:
            feats, src = run(n)
            if not feats:
                raise RuntimeError("0 features")
            if n != "estrato_manzanas" or True:
                p = write_fc(n, feats)
            man[n] = {"fetched": TODAY.isoformat(), "features": len(feats), "source": src}
            log(f"  -> {len(feats)} features, {p.stat().st_size/1024:.0f} KiB")
        except Exception as e:  # noqa
            log(f"  FAILED {n}: {e!r}")
            failed.append(n)
        MANIFEST.write_text(json.dumps(man, indent=1, ensure_ascii=False))
    if "osm_pois" in names:
        write_anchors()
    tot = sum(f.stat().st_size for f in OUT.iterdir())
    log(f"total data/layers: {tot/1e6:.1f} MB; failed: {failed}")


if __name__ == "__main__":
    main()

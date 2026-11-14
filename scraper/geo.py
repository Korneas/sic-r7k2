"""Enriquecimiento geográfico (PLAN §5.2, §6.2 C3/C4, §6.3): llena `listing_geo` para todas las ofertas.

Uso: .venv/bin/python -m scraper.geo   (o desde el pipeline de scraper.main)

- Todo se calcula en metros con una proyección equirectangular local alrededor de lat 3.43
  (error < 0,1 % en Cali). Capas: data/layers/*.geojson, cargadas una vez por proceso.
- Polígonos y líneas: shapely.STRtree (consultas vectorizadas). Puntos: kNN por fuerza bruta en numpy
  por bloques sobre las ubicaciones únicas (≤ ~3k candidatos por categoría).
- Punto efectivo: `exact` usa la coordenada del portal (si su barrio IDESC contradice al del portal y el
  del portal queda a > 1,5 km, se baja a `neighborhood`); `neighborhood` usa un punto interior del
  polígono del barrio del portal (coincidencia difusa por nombre); sin coincidencia, todo queda en null.
  La coordenada del portal en `listing` NO se toca: el punto efectivo va en listing_geo.lat/lng y el
  original en geo_note. Así la corrida es idempotente aunque upsert vuelva a marcar `exact`.
"""
import difflib
import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta

import numpy as np
import shapely
import yaml
from shapely.geometry import shape

from scraper import db

LAYERS = "data/layers"
ANCHORS = "config/anchors.yaml"
LAT0, LNG0 = 3.43, -76.53
M_LAT, M_LNG = 110574.0, 111320.0 * math.cos(math.radians(LAT0))

GEO_COLUMNS = {"lat": "REAL", "lng": "REAL", "amen_score": "REAL", "proj_score": "REAL",
               "amen_top": "TEXT", "geo_note": "TEXT"}

# C4 (PLAN §6.2): profundidad = cuántos de los mejores entran al promedio de la categoría
DEPTH = {"mio_station": 1, "mio_stop": 1, "mall": 1, "supermarket": 2, "park": 2, "hospital": 1,
         "school": 2, "restaurant": 10, "bank_pharmacy": 2, "fitness": 1}
DEFAULT_WEIGHTS = {"mio_station": 15, "mio_stop": 5, "mall": 15, "supermarket": 12, "park": 13, "hospital": 10,
                   "school": 10, "restaurant": 8, "bank_pharmacy": 4, "fitness": 3}
DEFAULT_PENALTIES = {"inund_nomit": 30, "inund": 12, "inund_baja": 4, "ladera": 15, "homic_p90": 15,
                     "homic_p75": 7, "precario": 10, "ruido": 5, "ruido_db": 65, "legal": 10}
LABEL = {"mio_station": "Estación MIO", "mio_stop": "Parada MIO", "mall": "Centro comercial",
         "supermarket": "Supermercado", "park": "Parque", "hospital": "Clínica", "school": "Colegio",
         "restaurant": "Restaurante", "bank_pharmacy": "Banco/droguería", "fitness": "Escenario deportivo"}
# C3 proyectos (PLAN §6.2): (radio m, puntos)
PROJ = {"renov": (500, 40), "plusvalia": (0, 25), "troncal": (800, 30), "tren": (1000, 15)}
HOMIC_RADIUS, HOMIC_MONTHS = 500, 24
SANITY_KM = 1.5
MANZANA_SNAP_M = 30  # los puntos suelen caer en la calle, entre manzanas

_CACHE = {}


# ---------------------------------------------------------------- utilidades
def to_m(lat, lng):
    return np.column_stack([(np.asarray(lng, float) - LNG0) * M_LNG, (np.asarray(lat, float) - LAT0) * M_LAT])


def to_ll(xy):
    xy = np.asarray(xy, float)
    return xy[..., 1] / M_LAT + LAT0, xy[..., 0] / M_LNG + LNG0


def _proj(coords):
    return (coords - [LNG0, LAT0]) * [M_LNG, M_LAT]


def _feats(name):
    p = os.path.join(LAYERS, f"{name}.geojson")
    if not os.path.exists(p):
        print(f"[geo] capa {name} no existe, se omite", file=sys.stderr)
        return []
    return [f for f in json.load(open(p, encoding="utf-8"))["features"] if f.get("geometry")]


def _geoms(feats):
    g = np.array([shape(f["geometry"]) for f in feats], dtype=object)
    g = shapely.transform(g, _proj)
    bad = ~shapely.is_valid(g)
    if bad.any():
        g[bad] = shapely.make_valid(g[bad])
    return g


class Layer:
    """Polígonos o líneas en metros + propiedades + STRtree."""

    def __init__(self, feats):
        self.props = [f["properties"] for f in feats]
        self.geoms = _geoms(feats) if feats else np.array([], dtype=object)
        self.tree = shapely.STRtree(self.geoms)

    def __len__(self):
        return len(self.props)

    def nearest(self, pts, max_dist):
        """(distancia mínima, índice) por punto; inf / -1 si no hay nada a ≤ max_dist (0 = dentro)."""
        d = np.full(len(pts), np.inf)
        idx = np.full(len(pts), -1)
        if not len(self) or not len(pts):
            return d, idx
        if max_dist <= 0:
            pi, gi = self.tree.query(pts, predicate="intersects")
            dd = np.zeros(len(pi))
        else:
            pi, gi = self.tree.query(pts, predicate="dwithin", distance=max_dist)
            dd = shapely.distance(pts[pi], self.geoms[gi])
        o = np.argsort(-dd, kind="stable")  # la última escritura (la menor) gana
        d[pi[o]], idx[pi[o]] = dd[o], gi[o]
        return d, idx


def knn(P, Q, k, chunk=2048):
    """k vecinos más cercanos de cada fila de P (n,2) en Q (m,2): (dist (n,k), idx (n,k)), ordenados."""
    k = min(k, len(Q))
    if not k:
        return np.zeros((len(P), 0)), np.zeros((len(P), 0), int)
    q2 = (Q ** 2).sum(1)
    D, I = np.empty((len(P), k)), np.empty((len(P), k), int)
    for s in range(0, len(P), chunk):
        p = P[s:s + chunk]
        d2 = (p ** 2).sum(1)[:, None] + q2[None, :] - 2 * p @ Q.T
        part = np.argpartition(d2, k - 1, axis=1)[:, :k] if k < len(Q) else np.tile(np.arange(len(Q)), (len(p), 1))
        dp = np.take_along_axis(d2, part, 1)
        o = np.argsort(dp, 1)
        I[s:s + chunk] = np.take_along_axis(part, o, 1)
        D[s:s + chunk] = np.sqrt(np.maximum(np.take_along_axis(dp, o, 1), 0))
    return D, I


def count_within(P, Q, r, chunk=2048):
    out = np.zeros(len(P), int)
    if not len(Q):
        return out
    q2 = (Q ** 2).sum(1)
    for s in range(0, len(P), chunk):
        p = P[s:s + chunk]
        d2 = (p ** 2).sum(1)[:, None] + q2[None, :] - 2 * p @ Q.T
        out[s:s + chunk] = (d2 <= r * r).sum(1)
    return out


# ---------------------------------------------------------------- nombres de barrio
# nombres de portal que no dicen nada del lugar ("Na" = no aplica en M2): se tratan como vacíos
GENERIC = {"na", "nn", "n n", "no aplica", "sin informacion", "sur", "norte", "oeste", "este", "zona norte",
           "zona sur", "zona oeste", "cali"}
COMUNA_SUFFIX = re.compile(r"\s*\bcomuna\s*\d+$")
TITLE_HOOD = re.compile(r"\ben\s+(?:venta|arriendo)\s*(?:,|\ben\b)\s*(.+)$", re.I)
BBOX = (3.20, 3.60, -76.75, -76.40)  # lat_min, lat_max, lng_min, lng_max: fuera de esto la coordenada no sirve (0,0)
PREFIX = re.compile(r"^(?:urbanizacion|urb|ub|barrio|br|bo|conjunto residencial|conjunto|conj|cr|unidad residencial|"
                    r"unidad|ur|residencial|condominio|edificio|sector|parcelacion|parcelaciones)\b\s*")
ALIASES = {"valle del lili": "lili"}  # nombres de portal -> nombre IDESC (normalizados); más en config geo.aliases
CARDINAL = re.compile(r"^(?:cali|norte|sur|oeste)\s+|\s+(?:norte|sur|oeste|alta|residencial|residential)$")


def variants(k):
    """La clave y, si cambia, la clave sin punto cardinal: "santa monica residencial" -> "santa monica"."""
    v = CARDINAL.sub("", k).strip()
    return [k, v] if v and v != k else [k]
ARTICLE = re.compile(r"^(?:el|la|los|las)\s+")


def name_key(s):
    s = re.sub(r"[^a-z0-9 ]", " ", db.norm(s))
    s = re.sub(r"\b(?:cali|valle del cauca|valle|colombia)$", "", " ".join(s.split())).strip()
    s = COMUNA_SUFFIX.sub("", s).strip()  # "Ciudad Melendez/ Comuna 18" -> "ciudad melendez"
    for _ in range(3):
        s2 = ARTICLE.sub("", PREFIX.sub("", s))
        if s2 == s:
            break
        s = s2
    s = " ".join(s.split())
    return "" if s in GENERIC else s


def title_hood(title):
    """Barrio del título: "Apartamento en Venta, Ciudad Pacifica, Cali" / "Casa en venta en Bochalema, Cali"."""
    m = TITLE_HOOD.search(title or "")
    return m.group(1).split(",")[0].strip() if m else None


class BarrioIndex:
    def __init__(self, layer):
        self.layer = layer
        self.keys = {}
        names = [p["barnombre"] or "" for p in layer.props]
        for i, full in enumerate(names):
            self.keys.setdefault(name_key(full), i)
        for i, full in enumerate(names):  # alias "A - B" => A, B (sin pisar nombres completos)
            for alias in re.split(r"\s+-\s+", full):
                self.keys.setdefault(name_key(alias), i)
        self.klist = list(self.keys)
        self._memo = {}
        self.aliases = dict(ALIASES)

    def set_aliases(self, extra):
        """Alias de config (geo.aliases): nombre de portal -> nombre IDESC; ambos se normalizan con name_key."""
        al = {**ALIASES, **{name_key(k): name_key(v) for k, v in (extra or {}).items()}}
        if al != self.aliases:
            self.aliases, self._memo = al, {}

    def match(self, name):
        """Índice del barrio cuyo nombre coincide con `name` (o None). "A - B" o "A, B" prueba cada parte."""
        i = self._match(name)
        if i is None and re.search(r"\s-\s|,|/|\\", name or ""):
            i = next((j for part in re.split(r"\s-\s|,|/|\\", name) if (j := self._match(part)) is not None), None)
        return i

    def _match(self, name):
        k = name_key(name)
        k = self.aliases.get(k, k)
        if not k:
            return None
        if k in self._memo:
            return self._memo[k]
        i = next((self.keys[v] for v in variants(k) if v in self.keys), None)
        if i is None and len(variants(k)) > 1:
            v = variants(k)[1]
            i = self.keys.get(self.aliases.get(v, v))
        if i is None:
            toks = set(k.split())
            subs = [c for c in self.klist if toks <= set(c.split()) and len(k) >= 4]  # "san fernando" ⊂ "san fernando viejo"
            if subs:
                i = self.keys[min(subs, key=len)]
            else:
                best = difflib.get_close_matches(k, self.klist, n=1, cutoff=0.86)
                i = self.keys[best[0]] if best else None
        self._memo[k] = i
        return i


# ---------------------------------------------------------------- carga de capas
def _anchors():
    try:
        return (yaml.safe_load(open(ANCHORS, encoding="utf-8")) or {}).get("malls") or []
    except FileNotFoundError:
        return []


class Pts:
    """Candidatos de una categoría de amenidad: coordenadas en m, nombre, multiplicador y radio (parques)."""

    def __init__(self):
        self.xy, self.name, self.mult, self.rad = [], [], [], []

    def add(self, lat, lng, name, mult=1.0, rad=0.0):
        self.xy.append(((lng - LNG0) * M_LNG, (lat - LAT0) * M_LAT))
        self.name.append(name)
        self.mult.append(mult)
        self.rad.append(rad)

    def done(self):
        self.xy = np.array(self.xy, float).reshape(-1, 2)
        self.mult, self.rad = np.array(self.mult, float), np.array(self.rad, float)
        return self


def _point_feats(name):
    for f in _feats(name):
        if f["geometry"]["type"] == "Point":
            lng, lat = f["geometry"]["coordinates"][:2]
            yield lat, lng, f["properties"]


def load_layers():
    if _CACHE:
        return _CACHE
    t = time.time()
    L = {}
    L["barrios"] = Layer(_feats("barrios"))
    L["barrio_idx"] = BarrioIndex(L["barrios"])
    L["comunas"] = Layer(_feats("comunas"))
    L["manzanas"] = Layer(_feats("estrato_manzanas"))
    eb = os.path.join(LAYERS, "estrato_barrios.json")
    L["estrato_barrios"] = json.load(open(eb)) if os.path.exists(eb) else {}

    # riesgos: inundación con severidad (3 no mitigable, 2 alta/media/torrencial, 1 baja)
    inund = []
    for f in _feats("inundacion_no_mitigable"):
        p = f["properties"]
        f["properties"] = {"sev": 3, "why": p.get("amenaza_in") or p.get("tipo_amena") or "no mitigable"}
        inund.append(f)
    for src in ("inundacion_fluvial", "inundacion_pluvial"):
        for f in _feats(src):
            t_ = f["properties"].get("tipo_amena") or ""
            n = db.norm(t_)
            kind = "pluvial" if "pluvial" in n else "fluvial" if ("fluvial" in n or "velocidad" in n or "torrencial" in n) \
                else src.split("_")[1]
            if "no mitigable" in n:
                sev = 3
            elif "alta" in n or "media" in n or "torrencial" in n:
                sev = 2
            elif "baja" in n:
                sev = 1
            else:
                continue  # "Área prioritaria para estudio"
            f["properties"] = {"sev": sev, "kind": kind, "why": re.sub(r"\s*\(.*$", "", t_).strip()}
            inund.append(f)
    L["inund"] = Layer(inund)
    L["ladera"] = Layer(_feats("pendientes"))
    L["precario"] = Layer([dict(f, properties={"name": f["properties"].get("bpnombre")}) for f in _feats("barrios_precarios")]
                          + [dict(f, properties={"name": None}) for f in _feats("mejoramiento")])
    L["ruido"] = Layer(_feats("ruido_noche"))

    # proyectos
    renov = [dict(f, properties={"name": f"Renovación urbana {f['properties']['codigo']}"})
             for f in _feats("tratamientos") if (f["properties"].get("codigo") or "") in ("R1", "R2", "R3")
             or "RENOVACION" in (f["properties"].get("tratamient") or "").upper()]
    seen = set()
    for src in ("planes_parciales", "ciudad_paraiso"):
        for f in _feats(src):
            nm = f["properties"].get("plan_parci")
            if nm not in seen:
                seen.add(nm)
                renov.append(dict(f, properties={"name": nm}))
    L["renov"] = Layer(renov)
    L["plusvalia"] = Layer([dict(f, properties={"name": f"Plusvalía {(f['properties'].get('nombre') or '').title()}".strip()})
                            for f in _feats("plusvalia")])
    L["troncal"] = Layer([dict(f, properties={"name": f"Troncal proyectada {f['properties'].get('nombre') or ''}".strip()})
                          for f in _feats("troncales_proyectadas")])
    L["tren"] = Layer([dict(f, properties={"name": f"Corredor férreo {f['properties'].get('tramo') or ''}".strip()})
                       for f in _feats("corredor_ferreo")])

    # homicidios recientes: ventana de 24 meses hasta la fecha más reciente de la capa
    hom = [(lat, lng, p.get("date") or "") for lat, lng, p in _point_feats("homicidios")]
    last = max((d for *_, d in hom if d), default=None)
    L["hom_window"] = None
    if last:
        start = (date.fromisoformat(last) - timedelta(days=round(HOMIC_MONTHS * 30.44))).isoformat()
        hom = [h for h in hom if h[2] >= start]
        L["hom_window"] = (start, last)
    L["hom"] = to_m([h[0] for h in hom], [h[1] for h in hom]) if hom else np.zeros((0, 2))

    # amenidades
    C = {c: Pts() for c in DEPTH}
    for lat, lng, p in _point_feats("mio_stops"):
        C["mio_station" if p.get("kind") == "station" else "mio_stop"].add(lat, lng, p.get("name"))
    anchors = _anchors()
    anchor_names = {db.norm(a.get("osm_name")) for a in anchors}
    for a in anchors:
        C["mall"].add(a["lat"], a["lng"], a["name"], 1.0)
    for lat, lng, p in _point_feats("osm_pois"):
        cat, nm = p.get("cat"), p.get("name")
        if cat == "station":
            C["mio_station"].add(lat, lng, nm)
        elif cat == "bus_stop":
            C["mio_stop"].add(lat, lng, nm)
        elif cat == "mall":
            if db.norm(nm) not in anchor_names:
                C["mall"].add(lat, lng, nm, 0.5)  # los grandes (anchors) pesan doble
        elif cat == "hospital":
            C["hospital"].add(lat, lng, nm, 1.0 if p.get("sub") == "hospital" else 0.6)
        elif cat == "park":
            a = p.get("area_m2")
            C["park"].add(lat, lng, nm, _park_mult(a), min(math.sqrt(a / math.pi), 500) if a else 0.0)
        elif cat in ("supermarket", "school", "restaurant", "fitness"):
            C[cat].add(lat, lng, nm)
        elif cat in ("bank", "pharmacy"):
            C["bank_pharmacy"].add(lat, lng, nm)
    for lat, lng, p in _point_feats("ips"):
        cla = db.norm(p.get("cla_ips"))
        if "transporte" in cla or "independiente" in cla or "objeto social" in cla:
            continue
        C["hospital"].add(lat, lng, _title(p.get("name")), 1.0 if (p.get("has_beds") or p.get("has_salas")) else 0.6)
    for lat, lng, p in _point_feats("colegios"):
        C["school"].add(lat, lng, p.get("name"))
    for lat, lng, p in _point_feats("escenarios_deportivos"):
        C["fitness"].add(lat, lng, _title(p.get("nombre")))
    rec = [f for f in _feats("recreacion") if f["properties"].get("tipo") in ("URECREA", "PARQUE", "ESCENARIO")
           and f["properties"].get("propiedad") != "PRIV"]
    L["park_poly"] = Layer(rec)
    for g, p in zip(L["park_poly"].geoms, L["park_poly"].props):
        a = g.area
        lat, lng = to_ll(shapely.get_coordinates(g.centroid)[0])
        C["park"].add(float(lat), float(lng), _title(p.get("nombre")), _park_mult(a), min(math.sqrt(a / math.pi), 500))
    L["amen"] = {c: v.done() for c, v in C.items()}
    print(f"[geo] capas cargadas en {time.time() - t:.1f}s: "
          + ", ".join(f"{c}={len(v.name)}" for c, v in L["amen"].items()))
    _CACHE.update(L)
    return _CACHE


def _park_mult(area):
    return float(np.clip(math.sqrt(area / 5000), 0.3, 1.0)) if area else 0.5


def _title(s):
    return s.title() if s and s.isupper() else s


# ---------------------------------------------------------------- puntajes
def decay(d, full=400, zero=2400):
    return np.clip((zero - d) / (zero - full), 0, 1)


def hospital_decay(d, full=400, zero=2400):
    """< 150 m = 0 (tráfico, ruido); 150–400 sube; 400–1.500 completo; luego baja a 0 en 1.500 + (zero − full)."""
    up = np.clip((d - 150) / (full - 150), 0, 1)
    down = np.clip((1500 + zero - full - d) / (zero - full), 0, 1)
    return np.minimum(up, down)


def amenities(P, L, cfg):
    """Por categoría: distancias caminables de los ítems usados, subpuntaje 0-100, top-3 con nombre; y amen_score."""
    sc = cfg.get("score", {})
    dc = sc.get("decay", {})
    full, zero, wf = dc.get("full_m", 400), dc.get("zero_m", 2400), dc.get("walk_factor", 1.3)
    W = {**DEFAULT_WEIGHTS, **(sc.get("amenity_weights") or {})}
    n = len(P)
    out = [dict() for _ in range(n)]
    top = [[] for _ in range(n)]
    total = np.zeros(n)
    for cat, depth in DEPTH.items():
        c = L["amen"][cat]
        if not len(c.name):
            for o in out:
                o[cat] = {"d": [], "s": 0}
            continue
        varying = cat in ("mall", "hospital", "park")
        D, I = knn(P, c.xy, depth + (10 if varying else 0) + 4)
        D = np.maximum(D - c.rad[I], 0) * wf  # a borde de parque y caminando
        val = (hospital_decay(D, full, zero) if cat == "hospital" else decay(D, full, zero)) * c.mult[I]
        o = np.argsort(-val, 1, kind="stable")[:, :depth]
        vbest = np.take_along_axis(val, o, 1)
        dbest = np.take_along_axis(D, o, 1)
        s = vbest.sum(1) / depth
        total += W.get(cat, 0) * s
        lab = LABEL[cat]
        for i in range(n):
            out[i][cat] = {"d": [int(round(x)) for x in dbest[i]], "s": int(round(100 * s[i]))}
            names = set()
            for j in range(D.shape[1]):  # ordenados por distancia
                nm = c.name[I[i, j]] or lab
                k = db.norm(nm)
                if k in names:
                    continue
                names.add(k)
                top[i].append({"cat": cat, "name": nm, "dist": int(round(D[i, j]))})
                if len(names) == 3:
                    break
    wsum = sum(W.get(c, 0) for c in DEPTH) or 1
    return out, top, np.round(100 * total / wsum, 1)


# ---------------------------------------------------------------- riesgos y proyectos
def _snippet(text, s, e, pad=35):
    a, b = max(0, s - pad), min(len(text), e + pad)
    while a > 0 and text[a - 1] != " ":
        a -= 1
    while b < len(text) and text[b] != " ":
        b += 1
    return ("…" if a > 0 else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def legal_risk(title, desc, rx, pts):
    text = db.norm(f"{title or ''}. {desc or ''}")
    m = rx.search(text) if rx else None
    if not m:
        return None
    return {"k": "legal", "pts": pts, "why": f"Posible tema legal: «{_snippet(text, m.start(), m.end())}»"}


def geo_risks(P, L, pen):
    """Riesgos que dependen solo del punto: lista por punto."""
    n = len(P)
    out = [[] for _ in range(n)]
    pts = shapely.points(P)
    sev = np.full(n, 0)
    if len(L["inund"]):  # el punto puede caer en varias capas: severidad máxima
        pi, gi = L["inund"].tree.query(pts, predicate="intersects")
        best = {}
        for a, b in zip(pi, gi):
            if L["inund"].props[b]["sev"] > sev[a]:
                sev[a], best[a] = L["inund"].props[b]["sev"], b
        for a, b in best.items():
            p = L["inund"].props[b]
            if p["sev"] == 3:
                out[a].append({"k": "inund_nomit", "pts": pen["inund_nomit"],
                               "why": f"Zona de amenaza no mitigable por inundación (POT 2014): {p['why']}"})
            else:
                kind = "río Cauca o tributarios" if p.get("kind") == "fluvial" else "pluvial"
                out[a].append({"k": "inund", "pts": pen["inund"] if p["sev"] == 2 else pen["inund_baja"],
                               "why": f"Zona inundable ({kind}): {p['why']}"})
    d, idx = L["ladera"].nearest(pts, 0)
    for a in np.nonzero(idx >= 0)[0]:
        pp = L["ladera"].props[idx[a]].get("pend_porc") or "≥ 25%"
        out[a].append({"k": "ladera", "pts": pen["ladera"],
                       "why": f"Ladera con pendiente alta, {pp} (proxy de remoción en masa)"})
    d, idx = L["precario"].nearest(pts, 0)
    for a in np.nonzero(idx >= 0)[0]:
        nm = L["precario"].props[idx[a]].get("name")
        out[a].append({"k": "precario", "pts": pen["precario"],
                       "why": "Entorno de mejoramiento integral" + (f": {nm}" if nm else "")})
    d, idx = L["ruido"].nearest(pts, 0)
    for a in np.nonzero(idx >= 0)[0]:
        dbv = L["ruido"].props[idx[a]].get("db")
        if dbv is not None and dbv >= pen["ruido_db"]:
            out[a].append({"k": "ruido", "pts": pen["ruido"], "why": f"Ruido nocturno alto: {dbv} dB (DAGMA)"})
    return out


def projects(P, L):
    n = len(P)
    pts = shapely.points(P)
    out = [[] for _ in range(n)]
    score = np.zeros(n)
    for k, (r, p) in PROJ.items():
        d, idx = L[k].nearest(pts, r)
        for a in np.nonzero(idx >= 0)[0]:
            out[a].append({"k": k, "name": L[k].props[idx[a]]["name"], "dist": int(round(d[a])), "pts": p})
            score[a] += p
    return out, np.minimum(score, 100)


# ---------------------------------------------------------------- corrida
def ensure_columns(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(listing_geo)")}
    for c, t in GEO_COLUMNS.items():
        if c not in have:
            conn.execute(f"ALTER TABLE listing_geo ADD COLUMN {c} {t}")


def _cfg(cfg):
    if cfg is None:
        cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    return cfg


def run(conn, cfg=None):
    t0 = time.time()
    cfg = _cfg(cfg)
    ensure_columns(conn)
    L = load_layers()
    B, bidx = L["barrios"], L["barrio_idx"]
    pen = {**DEFAULT_PENALTIES, **(cfg.get("score", {}).get("penalties") or {})}
    kws = [db.norm(k) for k in cfg.get("legal_keywords") or []]
    legal_rx = re.compile(r"\b(?:" + "|".join(map(re.escape, kws)) + r")\b") if kws else None

    rows = conn.execute("SELECT id, lat, lng, geo_precision, neighborhood_portal, title, description FROM listing").fetchall()
    n = len(rows)
    rep = {}  # punto interior de cada barrio (para ubicaciones por barrio)

    def rep_xy(i):
        if i not in rep:
            rep[i] = shapely.get_coordinates(shapely.point_on_surface(B.geoms[i]))[0]
        return rep[i]

    lat = np.array([r["lat"] if r["lat"] is not None else np.nan for r in rows], float)
    lng = np.array([r["lng"] if r["lng"] is not None else np.nan for r in rows], float)
    xy = to_m(lat, lng)
    with np.errstate(invalid="ignore"):  # (0, 0) u otra coordenada fuera de Cali = sin coordenada
        has = ~np.isnan(xy).any(1) & (lat >= BBOX[0]) & (lat <= BBOX[1]) & (lng >= BBOX[2]) & (lng <= BBOX[3])
    exact = np.array([r["geo_precision"] == "exact" for r in rows]) & has

    # barrio por polígono (con tolerancia de 50 m para puntos en el borde o en la vía)
    pts = shapely.points(np.where(has[:, None], xy, 0))
    _, bin_ = B.nearest(pts, 50)
    bin_[~has] = -1

    eff = np.full((n, 2), np.nan)
    precision = [r["geo_precision"] for r in rows]
    notes = [None] * n
    barrio = np.full(n, -1)
    downgraded = 0
    old_notes = {k: json.loads(v) for k, v in conn.execute(
        "SELECT listing_id, geo_note FROM listing_geo WHERE geo_note LIKE '%\"orig\"%'")}
    G = cfg.get("geo") or {}
    bidx.set_aliases(G.get("aliases"))
    cent = {name_key(k): v for k, v in (G.get("centroids") or {}).items()}
    portal_idx = [bidx.match(r["neighborhood_portal"]) if r["neighborhood_portal"] else None for r in rows]
    fallback = 0

    def locate(r):
        """Respaldo sin coordenada usable ni barrio IDESC del portal: centroide de config del nombre del portal,
        luego barrio IDESC o centroide del nombre que trae el título. -> (idx barrio | None, xy | None, nota)."""
        tn = title_hood(r["title"])
        for src, name in (("portal", r["neighborhood_portal"]), ("titulo", tn)):
            k = name_key(name or "")
            if not k:
                continue
            if src == "titulo":
                j = bidx.match(name)
                if j is not None:
                    return j, rep_xy(j), {"src": "titulo", "match": B.props[j]["barnombre"]}
            k = next((v for v in variants(k) if v in cent), None)
            if k:
                cxy = to_m([cent[k][0]], [cent[k][1]])[0]
                return None, cxy, {"src": "centroide", "match": name, "why": (
                    f"Barrio del portal sin polígono IDESC ({name}): mediana de las ofertas con coordenada exacta")}
        return None, None, None

    for i, r in enumerate(rows):
        pi = portal_idx[i]
        if precision[i] == "exact" and not exact[i]:
            precision[i] = "neighborhood"  # "exacta" en (0, 0) o fuera de Cali
        if exact[i]:
            eff[i], barrio[i] = xy[i], bin_[i]
            if pi is not None and bin_[i] >= 0 and pi != bin_[i]:
                if name_key(B.props[pi]["barnombre"]) != name_key(B.props[bin_[i]]["barnombre"]):
                    dist = shapely.distance(pts[i], B.geoms[pi])
                    if dist > SANITY_KM * 1000:
                        eff[i], barrio[i] = rep_xy(pi), pi
                        precision[i] = "neighborhood"
                        downgraded += 1
                        notes[i] = {"orig": [r["lat"], r["lng"]], "why": (
                            f"La coordenada cae en {B.props[bin_[i]]['barnombre']}, a {dist / 1000:.1f} km del barrio "
                            f"del portal ({r['neighborhood_portal']}): se usa el barrio del portal")}
        elif pi is not None:
            eff[i], barrio[i] = rep_xy(pi), pi
            prev = old_notes.get(r["id"])  # bajada en una corrida anterior: conservar la coordenada original
            notes[i] = prev if prev and prev.get("orig") == [r["lat"], r["lng"]] else \
                {"src": "barrio", "match": B.props[pi]["barnombre"]}
        else:
            j, cxy, note = locate(r)
            if cxy is None:
                notes[i] = {"why": f"Barrio del portal sin coincidencia en IDESC: {r['neighborhood_portal']!r}"}
                continue
            fallback += 1
            eff[i], notes[i] = cxy, note
            if j is None:  # el barrio IDESC (si lo hay) es el del polígono donde cae el centroide
                _, bj = B.nearest(shapely.points(cxy[None, :]), 50)
                j = bj[0]
            barrio[i] = j
    ok = ~np.isnan(eff).any(1)

    # cálculos por ubicación única
    U, inv = np.unique(np.round(eff[ok], 1), axis=0, return_inverse=True)
    inv = inv.ravel()
    print(f"[geo] {n} ofertas, {ok.sum()} con punto, {len(U)} ubicaciones únicas")
    upts = shapely.points(U)
    amen, top, ascore = amenities(U, L, cfg)
    risks_u = geo_risks(U, L, pen)
    proj_u, pscore = projects(U, L)
    hom = count_within(U, L["hom"], HOMIC_RADIUS)
    _, mz = L["manzanas"].nearest(upts, MANZANA_SNAP_M)
    _, com = L["comunas"].nearest(upts, 0)
    homl = hom[inv]
    p75, p90 = (np.percentile(homl, [75, 90]) if len(homl) else (np.inf, np.inf))
    hw = L["hom_window"]
    hw_txt = f" ({hw[0][:7]} a {hw[1][:7]})" if hw else ""

    out = []
    uix = np.full(n, -1)
    uix[np.nonzero(ok)[0]] = inv
    for i, r in enumerate(rows):
        u = uix[i]
        risks = []
        if u >= 0:
            risks = list(risks_u[u])
            h = int(hom[u])
            if h > 0 and h >= p90:
                risks.append({"k": "homic", "pts": pen["homic_p90"],
                              "why": f"{h} homicidios a 500 m en 24 meses{hw_txt} (≥ P90 de la ciudad: {p90:.0f})"})
            elif h > 0 and h >= p75:
                risks.append({"k": "homic", "pts": pen["homic_p75"],
                              "why": f"{h} homicidios a 500 m en 24 meses{hw_txt} (≥ P75 de la ciudad: {p75:.0f})"})
        lr = legal_risk(r["title"], r["description"], legal_rx, pen["legal"])
        if lr:
            risks.append(lr)
        if precision[i] != "exact":
            risks.append({"k": "approx", "pts": 0, "why": "Ubicación aproximada: el análisis de zona usa el barrio"})
        b = barrio[i]
        bp = B.props[b] if b >= 0 else {}
        code = bp.get("barcodigo")
        comuna = bp.get("barcomuna")
        if comuna is None and u >= 0 and com[u] >= 0:
            comuna = L["comunas"].props[com[u]].get("comcodigo")
        stratum = None
        if u >= 0 and precision[i] == "exact" and mz[u] >= 0:
            stratum = L["manzanas"].props[mz[u]].get("estrato")
        if stratum is None and code in L["estrato_barrios"]:
            stratum = L["estrato_barrios"][code]["estrato"]
        if u >= 0:
            elat, elng = to_ll(eff[i])
            vals = (json.dumps(amen[u], ensure_ascii=False), json.dumps(proj_u[u], ensure_ascii=False),
                    round(float(elat), 6), round(float(elng), 6), float(ascore[u]), float(pscore[u]),
                    json.dumps(top[u], ensure_ascii=False))
        else:
            vals = (None, None, None, None, None, None, None)
        out.append((r["id"], code, bp.get("barnombre"), comuna, stratum, vals[0],
                    json.dumps(risks, ensure_ascii=False), vals[1], vals[2], vals[3], vals[4], vals[5], vals[6],
                    json.dumps(notes[i], ensure_ascii=False) if notes[i] else None))

    conn.execute("DELETE FROM listing_geo")
    conn.executemany("INSERT INTO listing_geo (listing_id, barrio_code, barrio, comuna, stratum_official, amenities, "
                     "risks, projects, lat, lng, amen_score, proj_score, amen_top, geo_note) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    conn.executemany("UPDATE listing SET geo_precision=? WHERE id=?",
                     [(precision[i], r["id"]) for i, r in enumerate(rows) if precision[i] != r["geo_precision"]])
    conn.commit()
    stats = dict(n=n, located=int(ok.sum()), barrio=int((barrio >= 0).sum()),
                 stratum=sum(1 for o in out if o[4] is not None), downgraded=downgraded, fallback=fallback,
                 homic_p75=float(p75), homic_p90=float(p90), seconds=round(time.time() - t0, 1))
    print("[geo]", json.dumps(stats))
    return stats


def suggest_centroids(conn, cfg=None, min_n=5, max_spread_m=1000):
    """Tabla geo.centroids para config.yaml: barrios de portal sin polígono IDESC (expansión sur, Pance, sectores)
    -> mediana de las coordenadas exactas de las ofertas con ese nombre (en el portal o en el título). Solo nombres
    con ≥ min_n ofertas y dispersión (mediana de la distancia a la mediana) ≤ max_spread_m."""
    cfg = _cfg(cfg)
    bidx = load_layers()["barrio_idx"]
    bidx.set_aliases((cfg.get("geo") or {}).get("aliases"))
    pts = {}
    for r in conn.execute("SELECT lat, lng, neighborhood_portal, title FROM listing WHERE geo_precision = 'exact' "
                          "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?", BBOX):
        for nm in {r["neighborhood_portal"], title_hood(r["title"])}:
            k = name_key(nm or "")
            if k and bidx.match(nm) is None:
                pts.setdefault(k, []).append((r["lat"], r["lng"]))
    out = {}
    for k, v in sorted(pts.items()):
        a = np.array(v)
        med = np.median(a, 0)
        spread = float(np.median(np.hypot(*(to_m(a[:, 0], a[:, 1]) - to_m([med[0]], [med[1]])).T)))
        if len(v) >= min_n and spread <= max_spread_m:
            out[k] = (round(float(med[0]), 5), round(float(med[1]), 5), len(v), round(spread))
    return out


if __name__ == "__main__":
    c = db.connect()
    db.init(c)
    if "--centroids" in sys.argv:
        for k, (la, ln, nn, sp) in suggest_centroids(c).items():
            print(f"    {k}: [{la}, {ln}]   # n={nn}, dispersión {sp} m")
    else:
        run(c)

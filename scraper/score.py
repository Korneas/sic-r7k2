"""Puntaje de oportunidad (PLAN §6) en `listing_score`. Uso: python -m scraper.score

Por grupo de dedupe con al menos una venta activa: se puntúa la canónica (dedupe.canonical_ids) y la misma fila
se guarda para los demás miembros activos de venta.

1. Cordura: precio y área (≥ min_area_m2) y precio/m² dentro de score.sanity [pm_min, pm_max]; fuera de rango →
   score NULL + "sospechoso" (p. ej. arriendo publicado como venta).
2. Comparables (§6.2 C1): una oferta por grupo, venta, mismo tipo, activa o vista ≤ max_age_days, área ±area_tol,
   estrato ±stratum_tol (oficial, si no el del portal); si el sujeto tiene > good_age_years, sin obra nueva
   (edad ≤ new_build_age); máximo per_location por punto (un edificio no domina); radio 800 m → 1.500 m → mismo barrio hasta k ≥ k_min;
   máximo k_max más cercanos. Pesos: distancia 1/(1+d/400) × similitud de área. Se excluye el propio grupo.
   expected_m2 = mediana ponderada. Dos pasadas: la primera marca |z_MAD| > z_mad (log precio/m², escala con piso
   mad_floor) como sospechosos, que salen del pool de la segunda.
   T15: con score.expected_model = hedonic | blend, expected_m2 pasa a ser la predicción hedónica (scraper/hedonic.py,
   cruzada: el modelo que juzga a una oferta no la vio) o la media geométrica kNN^(1−w)·hedónico^w. El kNN sigue
   dando comparables, z_MAD, pocos_comparables y confianza.
3. C2: ARV_m2 = P75 de comparables en buen estado (edad ≤ good_age_years o palabras de renovado) si hay ≥ 5, si no
   P75 de todos (score.arv.method = hedonic: la misma oferta renovada y en la banda de buen estado).
   upside = ARV_m2/pm − 1. C3 = 0,6·tendencia + 0,4·proj_score. C4 = amen_score. C5 = liquidez.
4. C1 y C2 se normalizan como percentil 0-100 entre todas las puntuables de la ciudad (C2 = 0 si upside ≤ 0).
   score = clamp(Σ w·C − Σ penalizaciones, 0, 100) · (0,6 + 0,4·confianza).
"""
import json
import math
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import yaml

from scraper import db, dedupe, hedonic
from scraper.zones import comuna_key, sanity, today_cot, ts

LAT0 = 3.43
M_LAT, M_LNG = 110574.0, 111320.0 * math.cos(math.radians(LAT0))
CELL = 1500.0
DEFAULTS = dict(
    weights=dict(c1_discount=0.35, c2_upside=0.25, c3_zone=0.15, c4_amenities=0.15, c5_liquidity=0.10),
    comps=dict(k_min=8, k_max=20, radii_m=[800, 1500], area_tol=0.30, stratum_tol=1, max_age_days=90),
    sanity=dict(z_mad=4.0, mad_floor=0.2, k_floor=3),
    zone=dict(min_n=15, trend_min_snapshots=4, trend_min_days=60, trend_window_days=365, liquidity_min_days=30),
    good_age_years=15, remodel_age_years=20, conf_k=12, new_hours=48,
    arv=dict(cap_vs_expected=0.35, upside_cap=0.8, method="p75"), area_lote_min=600, mixto_pts=10,
    expected_model="knn", hedonic=dict(hedonic.DEFAULTS),
)
NULL_WHY = {"sin_precio_area": "sin precio o área", "area_minima": "área menor al mínimo",
            "pm_fuera_rango": "precio/m² fuera de rango", "sin_ubicacion": "sin ubicación",
            "sin_tipo": "sin tipo de inmueble", "pocos_comparables": "menos comparables que k_floor", "z_mad": "precio atípico vs comparables (|z_MAD|)",
            "area_lote": "el área publicada parece la del lote, no la construida",
            "fuera_de_cali": "el título ubica la oferta en otro municipio"}
# T13: áreas en el texto. Número de m² junto a "lote/terreno" y junto a "construido/a".
_NUM = r"(\d{1,2}(?:[.,]\d{3})+|\d{2,5})(?:[.,]\d{1,2})?"
_UNIT = r"\s*(?:m2|mts2|mts|mt2|metros|m²|m\b|mt\b)"
LOT_RX = re.compile(r"(?:lote|terreno)\D{0,20}?" + _NUM + _UNIT + "|" + _NUM + _UNIT + r"\s*(?:de |del |en )?(?:lote|terreno)")
BUILT_RX = re.compile(r"construid[ao]s?\D{0,20}?" + _NUM + "|" + _NUM + _UNIT + r"\s*(?:de )?(?:area )?construid")
# año de construcción en el texto ("construido en 1995", "construcción de 120 m² en el año 1995", "año de construcción 1990")
YEAR_RX = re.compile(r"(?:construid[oa]s?|construccion|edificad[oa])[^.\n]{0,40}?(?:en|del?)\s+(?:el\s+)?(?:ano\s+)?"
                     r"(19[4-9]\d|20[0-2]\d)\b|ano de construccion\W{0,3}(19[4-9]\d|20[0-2]\d)\b")
CITY_RX = re.compile(r",\s*([^,]+?)\s*$")


def _nums(rx, text):
    return [float(re.sub(r"[.,]", "", g)) for m in rx.finditer(text) for g in m.groups() if g]


def area_fix(r, text, lote_min):
    """Casas: si el área del portal coincide (±3%) con un número de "lote/terreno" del texto y el texto da otra área
    construida, se usa la construida; si no la da y el área es ≥ lote_min, el área se marca como de lote.
    -> (área efectiva o None si es de lote, nota o None)."""
    a = r["area_m2"]
    if r["property_type"] != "casa" or not a:
        return a, None
    lots = _nums(LOT_RX, text)
    if not any(abs(x - a) <= 0.03 * a for x in lots):
        return a, None
    built = [x for x in _nums(BUILT_RX, text) if 40 <= x < 0.97 * a]
    if built:
        b = max(built)
        return b, f"área construida {b:,.0f} m² tomada del texto (el portal da {a:,.0f} m², que es el lote)".replace(",", ".")
    if a >= lote_min:
        return None, None
    return a, None


def _cfg(cfg):
    s = cfg.get("score") or {}
    out = {k: (dict(v, **(s.get(k) or {})) if isinstance(v, dict) else s.get(k, v)) for k, v in DEFAULTS.items()}
    out["sanity"] = {**sanity(cfg), **DEFAULTS["sanity"], **(s.get("sanity") or {})}
    return out


def money_m(v):
    return f"${v / 1e6:,.1f}M".replace(",", "X").replace(".", ",").replace("X", ".")


def wmedian(v, w):
    o = np.argsort(v)
    c = np.cumsum(w[o])
    return float(v[o][np.searchsorted(c, 0.5 * c[-1])])


def pct_rank(vals, ref):
    """Percentil 0-100 de cada valor de `vals` dentro de `ref` (empates al medio)."""
    ref = np.sort(np.asarray(ref, float))
    if len(ref) < 2:
        return np.full(len(vals), 50.0)
    lo = np.searchsorted(ref, vals, "left")
    hi = np.searchsorted(ref, vals, "right")
    return 100.0 * (lo + 0.5 * (hi - lo)) / len(ref)


def load(conn, now, cfg, C):
    """Filas de venta (una por grupo) del pool de comparables + sujetos."""
    rows = [dict(r) for r in conn.execute(
        "SELECT l.id, COALESCE(l.group_id, l.id) gid, l.url, l.property_type, l.price, l.area_m2, l.age_years, "
        "l.stratum_portal, l.title, l.description, l.geo_precision, l.status, l.first_seen, l.last_seen, "
        "l.bedrooms, l.bathrooms, l.parking, l.floor, g.amenities, g.projects, "
        "g.lat glat, g.lng glng, g.barrio_code, g.barrio, g.comuna, g.stratum_official, g.amen_score, g.proj_score, "
        "g.risks FROM listing l LEFT JOIN listing_geo g ON g.listing_id = l.id WHERE l.operation = 'sale'")]
    by_gid = defaultdict(list)
    for r in rows:
        by_gid[r["gid"]].append(r)
    canon = dedupe.canonical_ids(conn, "sale")  # {gid: id}
    cutoff = now - timedelta(days=C["comps"]["max_age_days"])
    reps, members = [], {}
    for gid, ms in by_gid.items():
        act = [m for m in ms if m["status"] == "active"]
        if act:
            cid = canon.get(gid) or min(act, key=lambda m: (m["first_seen"] or "", m["id"]))["id"]
            rep = next(m for m in act if m["id"] == cid)
            members[gid] = [m["id"] for m in act]
        else:
            rep = max(ms, key=lambda m: (m["last_seen"] or "", m["id"]))
            ls = ts(rep["last_seen"])
            if ls is None or ls < cutoff:
                continue
        rep["active"] = bool(act)
        firsts = [m["first_seen"] for m in ms if m["first_seen"]]
        rep["group_first"] = ts(min(firsts)) if firsts else None
        reps.append(rep)
    return reps, members


def run(conn, cfg=None, now=None, validate=False):
    t0 = time.time()
    cfg = cfg or yaml.safe_load(open("config.yaml", encoding="utf-8"))
    C = _cfg(cfg)
    S, K = C["sanity"], C["comps"]
    now = now or datetime.now(timezone.utc)
    now_s = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    min_area = cfg.get("min_area_m2", 20)
    reno_kw = [db.norm(k) for k in cfg.get("renovated_keywords") or []]
    mix_kw = [db.norm(k) for k in cfg.get("mixed_use_keywords") or []]
    other_cities = {db.norm(k) for k in cfg.get("other_cities") or []}
    remo_kw = [db.norm(k) for k in cfg.get("remodel_keywords") or []]

    reps, members = load(conn, now, cfg, C)
    n = len(reps)
    # ---------- arreglos ----------
    pm = np.full(n, np.nan)
    texts = [db.norm(f"{r['title'] or ''} {r['description'] or ''}") for r in reps]
    area = np.full(n, np.nan)
    area_note = [None] * n
    null_why = [None] * n
    for i, r in enumerate(reps):
        a, area_note[i] = area_fix(r, texts[i], C["area_lote_min"])
        p = r["price"]
        cm = CITY_RX.search(r["title"] or "")
        if cm and db.norm(cm.group(1)) in other_cities:
            null_why[i] = "fuera_de_cali"
        elif r["area_m2"] and a is None:
            null_why[i] = "area_lote"
        elif not a or not p:
            null_why[i] = "sin_precio_area"
        elif a < min_area:
            null_why[i] = "area_minima"
        else:
            area[i] = a
            pm[i] = p / a
            if not S["pm_min"] <= pm[i] <= S["pm_max"]:
                null_why[i] = "pm_fuera_rango"
        if null_why[i] is None and r["glat"] is None:
            null_why[i] = "sin_ubicacion"
        if null_why[i] is None and not r["property_type"]:
            null_why[i] = "sin_tipo"
    lat = np.array([r["glat"] if r["glat"] is not None else np.nan for r in reps], float)
    lng = np.array([r["glng"] if r["glng"] is not None else np.nan for r in reps], float)
    X, Y = (lng - (-76.53)) * M_LNG, (lat - LAT0) * M_LAT
    strat = np.array([r["stratum_official"] if r["stratum_official"] is not None else
                      (r["stratum_portal"] if r["stratum_portal"] is not None else np.nan) for r in reps], float)
    age = np.array([r["age_years"] if r["age_years"] is not None else np.nan for r in reps], float)
    # T13: si el texto da un año de construcción más viejo (> 8 años) que la edad del portal, manda el texto
    # (M2 "entre 5 y 10 años" en un edificio de 1995 metía obra nueva en su ARV)
    this_year = int(today_cot(cfg, now)[:4])
    age_text = 0
    for i in range(n):
        m = YEAR_RX.search(texts[i])
        if m:
            a = this_year - int(m.group(1) or m.group(2))
            if 0 <= a and (np.isnan(age[i]) or a > age[i] + 8):
                age[i] = a
                age_text += 1
    n_area = sum(1 for x in area_note if x)
    print(f"[score] T13: edad tomada del texto en {age_text}, área construida del texto en {n_area}")
    good = np.array([(age[i] <= C["good_age_years"]) or any(k in texts[i] for k in reno_kw) for i in range(n)])
    remodel = [bool(age[i] > C["remodel_age_years"]) or any(k in texts[i] for k in remo_kw) for i in range(n)]
    mixto = [next((k for k in mix_kw if k in texts[i]), None) for i in range(n)]
    lpm = np.log(np.where(np.isnan(pm), 1.0, pm))
    pool_ok = np.array([null_why[i] is None for i in range(n)])  # válidas como comparables

    # ---------- índice espacial por tipo ----------
    types = sorted({r["property_type"] for r in reps if r["property_type"]})
    grid, barrio_ix = {}, {}
    for t in types:
        g = defaultdict(list)
        b = defaultdict(list)
        for i, r in enumerate(reps):
            if r["property_type"] == t and pool_ok[i]:
                g[(int(X[i] // CELL), int(Y[i] // CELL))].append(i)
                if r["barrio_code"]:
                    b[r["barrio_code"]].append(i)
        grid[t] = {c: np.array(v) for c, v in g.items()}
        barrio_ix[t] = {c: np.array(v) for c, v in b.items()}
    near_cache = {}

    def neighbors(t, cx, cy):
        key = (t, cx, cy)
        if key not in near_cache:
            parts = [grid[t][c] for c in ((cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)) if c in grid[t]]
            near_cache[key] = np.concatenate(parts) if parts else np.empty(0, int)
        return near_cache[key]

    gid_arr = np.array([r["gid"] for r in reps], object)
    r1, r2 = K["radii_m"][0], K["radii_m"][-1]

    loc_m, per_loc = K.get("loc_m", 15), K.get("per_location", 2)

    def cap_loc(c, d):
        """Máximo `per_loc` comparables por punto (~loc_m): un edificio nuevo con muchas unidades (o el punto
        interior de un barrio, donde caen todas las aproximadas) no puede dominar la mediana ni el P75."""
        if len(c) <= per_loc:
            return c, d
        kk = np.round(X[c] / loc_m).astype(np.int64) * 1_000_003 + np.round(Y[c] / loc_m).astype(np.int64)
        _, inv = np.unique(kk, return_inverse=True)
        o = np.lexsort((np.arange(len(inv)), inv))
        si = inv[o]
        start = np.r_[0, np.flatnonzero(np.diff(si)) + 1]
        rank = np.empty(len(inv), int)
        rank[o] = np.arange(len(inv)) - np.repeat(start, np.diff(np.r_[start, len(si)]))
        keep = rank < per_loc
        return c[keep], d[keep]

    def comps(i, valid):
        """(idx ordenados por distancia, dist, etapa) — etapa ∈ {r1, r2, 'barrio'}."""
        r = reps[i]
        t = r["property_type"]

        def filt(c):
            c = c[valid[c] & (gid_arr[c] != r["gid"])]
            c = c[np.abs(area[c] - area[i]) <= K["area_tol"] * area[i]]
            if not np.isnan(strat[i]):
                s = strat[c]
                c = c[np.isnan(s) | (np.abs(s - strat[i]) <= K["stratum_tol"])]
            # usado (edad conocida > new_build_age; antes > good_age_years) no se compara con obra nueva: prima de
            # estreno que no da un remodelado. No al revés: la edad "nueva" del portal falla seguido (M2 "entre 5 y 10")
            # y filtrar los usados de una "nueva" infla su descuento.
            if age[i] > K.get("new_build_age", 8):
                c = c[~(age[c] <= K.get("new_build_age", 8))]
            return c

        c = filt(neighbors(t, int(X[i] // CELL), int(Y[i] // CELL)))
        d = np.hypot(X[c] - X[i], Y[c] - Y[i])
        o = np.argsort(d, kind="stable")
        c, d = cap_loc(c[o], d[o])
        for rad in (r1, r2):
            m = d <= rad
            if m.sum() >= K["k_min"]:
                return c[m][:K["k_max"]], d[m][:K["k_max"]], rad
        best = (c[d <= r2], d[d <= r2], r2)
        if r["barrio_code"] in barrio_ix[t]:
            cb = filt(barrio_ix[t][r["barrio_code"]])
            cb = np.union1d(cb, best[0]).astype(int)
            db_ = np.hypot(X[cb] - X[i], Y[cb] - Y[i])
            o = np.argsort(db_, kind="stable")
            cb, db_ = cap_loc(cb[o], db_[o])
            if len(cb) > len(best[0]):
                best = (cb[:K["k_max"]], db_[:K["k_max"]], "barrio")
        return best[0][:K["k_max"]], best[1][:K["k_max"]], best[2]

    def stats(i, c, d):
        wd = 1.0 / (1.0 + d / 400.0)
        wa = 1.0 - 0.7 * np.minimum(1.0, np.abs(area[c] - area[i]) / (K["area_tol"] * area[i]))
        w = wd * wa
        exp = wmedian(pm[c], w)
        mad = float(np.median(np.abs(lpm[c] - math.log(exp))))
        sig = max(1.4826 * mad, S["mad_floor"])
        z = (lpm[i] - math.log(exp)) / sig
        return exp, z

    # ---------- pasada 1: atípicos dentro del pool ----------
    susp = np.zeros(n, bool)
    for i in np.nonzero(pool_ok)[0]:
        c, d, _ = comps(i, pool_ok)
        if len(c) >= S["k_floor"]:
            _, z = stats(i, c, d)
            susp[i] = abs(z) > S["z_mad"]
    valid2 = pool_ok & ~susp

    # ---------- zonas (C3, C5) ----------
    zone = zone_context(conn, cfg, C, now)

    # ---------- v2 hedónico (T15): predicción cruzada; el kNN sigue dando comparables, z_MAD y confianza ----------
    model, H = C["expected_model"], C["hedonic"]
    hp = None
    act = np.array([r["active"] for r in reps])
    if model != "knn" or C["arv"].get("method") == "hedonic" or validate:
        exact = np.array([r["geo_precision"] == "exact" for r in reps])
        train = valid2 & act & (exact if H["train"] == "exact" else True)
        if train.sum() >= H["min_train"]:
            hedonic.DEFAULTS.update(H)
            reno = [any(k in texts[i] for k in reno_kw) for i in range(n)]
            remo_t = [any(k in texts[i] for k in remo_kw) for i in range(n)]
            F = hedonic.frame(reps, np.where(np.isnan(pm), 1.0, pm), area, age, strat, reno, remo_t,
                              [m is not None for m in mixto], X, Y)
            HP, HA, hfold, hmodel = hedonic.crossfit(F, train, np.nonzero(valid2 & act)[0], H["folds"])
            hp = True
            print(f"[score] hedónico: {int(train.sum())} de entrenamiento, modelo={model}, ARV={C['arv'].get('method')}")
        else:
            print(f"[score] hedónico: solo {int(train.sum())} para entrenar (< {H['min_train']}): se usa kNN")

    # ---------- pasada 2: sujetos ----------
    subj = [i for i, r in enumerate(reps) if r["active"]]
    res = {}
    for i in subj:
        r = reps[i]
        if null_why[i] is None and susp[i]:
            null_why[i] = "z_mad"
        if null_why[i] is not None:
            continue
        c, d, stage = comps(i, valid2)
        if len(c) < S["k_floor"]:
            null_why[i] = "pocos_comparables"
            continue
        exp, z = stats(i, c, d)
        if abs(z) > S["z_mad"]:
            null_why[i] = "z_mad"
            continue
        exp_knn = exp
        hed = math.exp(HP[hfold[i], i]) if hp is not None and np.isfinite(HP[hfold[i], i]) else None
        if hed and model == "hedonic":
            exp = hed
        elif hed and model == "blend":  # media geométrica ponderada
            exp = exp_knn ** (1 - H["blend_w"]) * hed ** H["blend_w"]
        # ARV (T13): un usado remodelado no cobra la prima de estreno -> sin obra nueva en el pool salvo que el
        # sujeto sea obra nueva; P75 de los de buen estado (≥ 5) o del pool; tope = expected × (1 + cap_vs_expected)
        nb = K.get("new_build_age", 8)
        pool = c if age[i] <= nb else c[~(age[c] <= nb)]
        if len(pool) < 3:
            pool = c
        g = pool[good[pool]]
        arv_p75 = float(np.percentile(pm[g if len(g) >= 5 else pool], 75))
        arv_hed = exp * math.exp(HA[hfold[i], i] - HP[hfold[i], i]) if hed else None
        arv_raw = arv_hed if arv_hed and C["arv"].get("method") == "hedonic" else arv_p75
        arv = min(arv_raw, exp * (1 + C["arv"]["cap_vs_expected"]))
        up = arv / pm[i] - 1
        res[i] = dict(c=c, d=d, stage=stage, exp=exp, exp_knn=exp_knn, exp_hed=hed, arv=arv, arv_p75=arv_p75,
                      arv_hed=arv_hed, disc=1 - pm[i] / exp,
                      up=min(up, C["arv"]["upside_cap"]), up_capped=up > C["arv"]["upside_cap"], arv_capped=arv < arv_raw)

    if validate:
        skey = np.array([(r["comuna"] and f"C{r['comuna']}") or f"g{np.nan_to_num(X[i]) // 3000:.0f}_{np.nan_to_num(Y[i]) // 3000:.0f}"
                         for i, r in enumerate(reps)])
        return hedonic.validate(F, train, res, pm, skey, hmodel, C, cfg)
    ok_ix = sorted(res)
    disc = np.array([res[i]["disc"] for i in ok_ix])
    ups = np.array([res[i]["up"] for i in ok_ix])
    p1 = pct_rank(disc, disc)
    p2 = np.where(ups > 0, pct_rank(ups, ups[ups > 0]) if (ups > 0).any() else 0, 0.0)
    W = C["weights"]
    w = [W["c1_discount"], W["c2_upside"], W["c3_zone"], W["c4_amenities"], W["c5_liquidity"]]

    # rebajas (precio actual vs primer precio de la canónica)
    first_price = {}
    for lid, p in conn.execute("SELECT listing_id, price FROM price_change ORDER BY listing_id, seen_at"):
        first_price.setdefault(lid, p)

    out, scored = [], []
    for i in subj:
        r = reps[i]
        tags = []
        fs = r["group_first"]
        dom = (now - fs).days if fs else None
        if fs and (now - fs).total_seconds() < C["new_hours"] * 3600:
            tags.append("nueva")
        fp = first_price.get(r["id"])
        pd = round(r["price"] / fp - 1, 4) if fp and r["price"] and fp != r["price"] else None
        if pd is not None and pd < 0:
            tags.append("rebaja")
        if remodel[i]:
            tags.append("remodelar")
        risks = json.loads(r["risks"]) if r["risks"] else []
        if any(x.get("k") == "legal" for x in risks):
            tags.append("legal")
        pens = [x for x in risks if (x.get("pts") or 0) > 0]
        if mixto[i]:  # T13: casa comercial / multifamiliar: los comparables residenciales no aplican bien
            tags.append("mixto")
            pens.append({"k": "mixto", "pts": (cfg.get("score", {}).get("penalties") or {}).get("mixto", C["mixto_pts"]),
                         "why": f"Uso mixto o multifamiliar («{mixto[i]}»): los comparables residenciales sobrevaloran el descuento"})
        comp = dict(c=None, discount=None, upside=None, tags=tags, pd=pd, dom=dom, k=0, comps_info=None)
        row = dict(score=None, comp=comp, pens=pens, exp=None, arv=None, flip=None, comps=[], conf=None)
        if i in res:
            out.append((i, res[i], row))
            continue
        if null_why[i] in ("pm_fuera_rango", "z_mad"):
            tags.append("sospechoso")
        comp["why_null"] = null_why[i]
        scored.append((i, row))
    pos = {i: j for j, i in enumerate(ok_ix)}
    for i, x, row in out:
        r = reps[i]
        comp = row["comp"]
        j = pos[i]
        z3, z5, notes = zone(r)
        c4 = float(r["amen_score"] or 0.0)
        cs = [float(p1[j]), float(p2[j]), z3, c4, z5]
        base = sum(a * b for a, b in zip(w, cs))
        pen = sum(p["pts"] for p in row["pens"])
        k = len(x["c"])
        exact = r["geo_precision"] == "exact"
        conf = min(1.0, k / C["conf_k"]) * (1.0 if exact else 0.7) * (1.0 if area[i] > 0 else 0.0)
        score = max(0.0, min(100.0, base - pen)) * (0.6 + 0.4 * conf)
        if x["disc"] <= 0:
            comp["tags"].append("no_oportunidad")
        where = f"a ≤ {x['stage']:,.0f} m".replace(",", ".") if x["stage"] != "barrio" else "en el mismo barrio"
        info = f"{k} comparables {where}, mediana {money_m(x['exp_knn'])}/m²"
        if x["exp_hed"] and model != "knn":
            info += f"; modelo hedónico {money_m(x['exp_hed'])}/m²"
            comp.update(exp_knn=round(x["exp_knn"]), exp_hed=round(x["exp_hed"]))
        if area_note[i]:
            info += f" · {area_note[i]}"
        comp.update(c=[round(v, 1) for v in cs], discount=round(x["disc"], 4), upside=round(x["up"], 4), k=k,
                    comps_info=info)
        if x["up_capped"]:
            comp["upside_capped"] = True
        if x["arv_capped"]:
            comp["arv_capped"] = True
        if notes:
            comp["notes"] = notes
        ar = float(area[i])
        row.update(score=round(score, 2), exp=round(x["exp"]), arv=round(x["arv"]), conf=round(conf, 3),
                   flip=dict(max_offer_70=round(0.7 * x["arv"] * ar), upside_cop=round((x["arv"] - pm[i]) * ar)),
                   comps=[dict(id=reps[c]["id"], lat=reps[c]["glat"], lng=reps[c]["glng"], pm=round(pm[c]),
                               a=float(area[c]), dist=int(round(dd)), url=reps[c]["url"])
                          for c, dd in zip(x["c"][:12], x["d"][:12])])
        scored.append((i, row))

    rows = []
    for i, row in scored:
        r = reps[i]
        vals = (now_s, row["score"], json.dumps(row["comp"], ensure_ascii=False),
                json.dumps(row["pens"], ensure_ascii=False), row["exp"], row["arv"],
                json.dumps(row["flip"]) if row["flip"] else None, json.dumps(row["comps"], ensure_ascii=False),
                row["conf"])
        for lid in members.get(r["gid"], [r["id"]]):
            rows.append((lid, *vals))
    conn.execute("DELETE FROM listing_score")
    conn.executemany("INSERT INTO listing_score (listing_id, computed_at, score, components, penalties, expected_m2, "
                     "arv_m2, flip, comps, confidence) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    summary(reps, scored, null_why, len(rows), time.time() - t0)
    return dict(groups=len(scored), rows=len(rows), scored=sum(1 for _, x in scored if x["score"] is not None))


def zone_context(conn, cfg, C, now):
    """Devuelve f(rep) -> (C3, C5, notas). Tendencia y liquidez desde zone_stats (barrio o, si n < min_n, comuna)."""
    Z = C["zone"]
    today = date.fromisoformat(today_cot(cfg, now))
    rows = conn.execute("SELECT barrio_code, date, type, n_sale, median_m2_sale, median_dom, absorption "
                        "FROM zone_stats ORDER BY date").fetchall()
    series = defaultdict(list)
    latest = {}
    first_day = None
    for b, d, t, n_, m, dom, ab in rows:
        dd = date.fromisoformat(d)
        first_day = dd if first_day is None or dd < first_day else first_day
        if (today - dd).days <= Z["trend_window_days"] and m:
            series[(b, t)].append(((dd - today).days, math.log(m)))
        latest[(b, t)] = (n_, dom, ab)
    slope = {}
    for key, s in series.items():
        if len(s) >= Z["trend_min_snapshots"] and s[-1][0] - s[0][0] >= Z["trend_min_days"]:
            a = np.array(s)
            slope[key] = float(np.polyfit(a[:, 0], a[:, 1], 1)[0]) * 365  # log por año
    keys = list(slope)
    sp = dict(zip(keys, pct_rank(np.array([slope[k] for k in keys]), [slope[k] for k in keys]))) if keys else {}
    liq_on = first_day is not None and (today - first_day).days >= Z["liquidity_min_days"]
    liq = {}
    if liq_on:
        lk = [k for k, v in latest.items() if v[1] is not None or v[2] is not None]
        doms = [latest[k][1] for k in lk if latest[k][1] is not None]
        abss = [latest[k][2] for k in lk if latest[k][2] is not None]
        for k in lk:
            parts = []
            if latest[k][1] is not None and doms:
                parts.append(100 - pct_rank([latest[k][1]], doms)[0])
            if latest[k][2] is not None and abss:
                parts.append(pct_rank([latest[k][2]], abss)[0])
            liq[k] = float(np.mean(parts))

    def f(r):
        t = r["property_type"]
        b, c = r["barrio_code"], comuna_key(r["comuna"])
        key = None
        if b and (b, t) in latest and (latest[(b, t)][0] or 0) >= Z["min_n"]:
            key = (b, t)
        elif c:
            key = (c, t)
        notes = []
        trend = sp.get(key)
        if trend is None:
            trend = 50.0
            notes.append("Tendencia de zona: sin datos aún")
        c3 = 0.6 * trend + 0.4 * float(r["proj_score"] or 0.0)
        c5 = liq.get(key)
        if c5 is None:
            c5 = 50.0
            notes.append("Liquidez: sin datos aún" if not liq_on else "Liquidez: sin retiros en la zona")
        return round(c3, 1), round(c5, 1), notes

    return f


def summary(reps, scored, null_why, nrows, secs):
    ok = [(i, x) for i, x in scored if x["score"] is not None]
    nulls = defaultdict(int)
    for i, x in scored:
        if x["score"] is None:
            nulls[null_why[i]] += 1
    s = np.array([x["score"] for _, x in ok]) if ok else np.zeros(0)
    opp = [(i, x) for i, x in ok if "no_oportunidad" not in x["comp"]["tags"]]
    print(f"[score] {len(scored)} grupos activos ({nrows} filas): {len(ok)} con puntaje, {len(opp)} ranqueables "
          f"(descuento > 0), {len(scored) - len(ok)} NULL en {secs:.1f}s")
    if nulls:
        print("[score] NULL por:", ", ".join(f"{k}={v}" for k, v in sorted(nulls.items(), key=lambda kv: -kv[1])))
    if len(s):
        q = np.percentile(s, [0, 10, 25, 50, 75, 90, 100])
        print("[score] distribución p0/p10/p25/p50/p75/p90/p100:", " / ".join(f"{v:.1f}" for v in q))
    top = sorted(opp, key=lambda ix: -ix[1]["score"])[:10]
    for i, x in top:
        r = reps[i]
        print(f"  {r['id']:<16} {str(r['barrio'])[:22]:<22} {r['property_type'][:4]} ${r['price'] / 1e6:,.0f}M "
              f"{r['area_m2']:.0f}m² pm={r['price'] / r['area_m2'] / 1e6:.2f}M "
              f"desc={x['comp']['discount']:+.0%} up={x['comp']['upside']:+.0%} k={x['comp']['k']} "
              f"conf={x['conf']:.2f} s={x['score']:.1f} {','.join(x['comp']['tags'])}")


if __name__ == "__main__":
    c = db.connect()
    db.init(c)
    run(c)

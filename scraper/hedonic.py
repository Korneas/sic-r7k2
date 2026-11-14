"""v2 (PLAN §6.4, T15): modelo hedónico de log(precio/m²) con Huber IRLS + ridge, en numpy.

Un modelo por tipo (apartamento, casa). Variables: log área (y su cuadrado), habitaciones, baños, parqueaderos,
bandas de edad, piso (aptos), estrato (dummies), log distancia a la amenidad más cercana de cada categoría
(listing_geo.amenities: la estación MIO es una categoría), proyectos (renovación, plusvalía, troncal, tren), riesgos
(inundación, ladera, homicidios P75/P90, precario, ruido), ubicación aproximada y palabras (renovado, remodelar, mixto).
Efectos de comuna y de barrio como one-hot con ridge más fuerte: el barrio es un efecto aleatorio que se encoge hacia
su comuna cuando tiene pocas ofertas (λ_barrio / λ_comuna en score.hedonic).

Producción (score.py): predicción cruzada por k pliegues aleatorios, así ninguna oferta entra al modelo que la juzga
(equivalente al leave-self-out del kNN). ARV hedónico = misma oferta con "renovado" y la edad en la banda de buen estado.

Validación: `python -m scraper.hedonic` (no escribe listing_score) -> MAPE fuera de muestra vs kNN, pesos implícitos de
amenidades y data/hedonic.json.
"""
import json
import math

import numpy as np

AMEN = ["mio_station", "mio_stop", "mall", "supermarket", "park", "hospital", "school", "restaurant",
        "bank_pharmacy", "fitness"]
AGE_BANDS = [0, 3, 8, 15, 25, 40]          # [0,3) [3,8) [8,15) [15,25) [25,40) [40,∞); referencia 8-15
AGE_REF, AGE_GOOD = 2, 2                   # banda de referencia y de "buen estado" para el ARV
RISKS = ["inund", "ladera", "homic_p75", "homic_p90", "precario", "ruido"]
PROJS = ["renov", "plusvalia", "troncal", "tren"]
DEFAULTS = dict(lam=1.0, lam_comuna=2.0, lam_barrio=2.0, delta=1.345, folds=5, min_train=400, train="active",
                cell_m=300, lam_cell=2.0,
                blend_w=0.4)


def _num(v):
    return np.array([np.nan if x is None else float(x) for x in v])


def frame(reps, pm, area, age, strat, reno, remo, mixto, X, Y):
    """Arreglos crudos por oferta (n = len(reps)); el diseño se arma por tipo en `design`."""
    n = len(reps)
    D = np.full((n, len(AMEN)), np.nan)
    risk = np.zeros((n, len(RISKS)))
    proj = np.zeros((n, len(PROJS)))
    for i, r in enumerate(reps):
        am = json.loads(r["amenities"]) if r.get("amenities") else {}
        for j, c in enumerate(AMEN):
            d = (am.get(c) or {}).get("d")
            if d:
                D[i, j] = min(d)
        for x in json.loads(r["risks"]) if r.get("risks") else []:
            k = x.get("k")
            k = "inund" if k in ("inund", "inund_nomit") else ("homic_p90" if k == "homic" and x.get("pts", 0) >= 15
                                                                else "homic_p75" if k == "homic" else k)
            if k in RISKS:
                risk[i, RISKS.index(k)] = 1
        for x in json.loads(r["projects"]) if r.get("projects") else []:
            if x.get("k") in PROJS:
                proj[i, PROJS.index(x["k"])] = 1
    return dict(
        y=np.log(pm), type=np.array([r["property_type"] or "" for r in reps]), larea=np.log(area),
        beds=_num([r.get("bedrooms") for r in reps]), baths=_num([r.get("bathrooms") for r in reps]),
        park=_num([r.get("parking") for r in reps]), floor=_num([r.get("floor") for r in reps]),
        age=np.asarray(age, float), strat=np.asarray(strat, float), D=D, risk=risk, proj=proj,
        approx=np.array([r.get("geo_precision") != "exact" for r in reps], float),
        reno=np.asarray(reno, float), remo=np.asarray(remo, float), mixto=np.asarray(mixto, float),
        barrio=np.array([str(r.get("barrio_code") or "") for r in reps]),
        comuna=np.array([str(r.get("comuna") or "") for r in reps]), X=np.asarray(X, float), Y=np.asarray(Y, float))


def cells(F):
    """Celda de cell_m metros: efecto local más fino que el barrio (también con ridge)."""
    c = DEFAULTS["cell_m"] or 1
    return np.array(["" if not np.isfinite(x + y) else f"{x // c:.0f}_{y // c:.0f}" for x, y in zip(F["X"], F["Y"])])


def age_band(age):
    b = np.searchsorted(AGE_BANDS, np.nan_to_num(age, nan=-1), side="right") - 1
    return np.where(np.isnan(age), -1, b)  # -1 = sin dato


def design(F, ix, levels=None, age_override=None, reno_override=None):
    """Matriz de diseño de las filas `ix` + vector de penalización. `levels` (del entrenamiento) fija las columnas.
    -> X, pen, names, levels."""
    first = levels is None
    if first:
        med = {k: float(np.nanmedian(F[k][ix])) if np.isfinite(F[k][ix]).any() else 0.0
               for k in ("beds", "baths", "park", "floor", "larea")}
        dmed = np.array([np.nanmedian(F["D"][ix, j]) if np.isfinite(F["D"][ix, j]).any() else 1000.0
                         for j in range(len(AMEN))])
        F["cell"] = cells(F)
        lv = lambda a: sorted(set(a[ix]) - {""})
        levels = dict(med=med, dmed=dmed, comuna=lv(F["comuna"]), barrio=lv(F["barrio"]), cell=lv(F["cell"]),
                      strat=[s for s in range(1, 7) if s != 4 and (F["strat"][ix] == s).any()],
                      apt=bool((F["type"][ix] == "apartamento").mean() > 0.5))
    med, L = levels["med"], levels
    cols, names, pens = [], [], []
    lam = DEFAULTS["lam"]

    def add(v, name, p=lam):
        cols.append(np.asarray(v, float))
        names.append(name)
        pens.append(p)

    def filled(k, lo, hi):
        v = F[k][ix]
        return np.clip(np.where(np.isnan(v), med[k], v), lo, hi), np.isnan(v).astype(float)

    add(np.ones(len(ix)), "intercept", 0.0)
    la = F["larea"][ix] - med["larea"]
    add(la, "log_area")
    add(la ** 2, "log_area2")
    for k, lo, hi in (("beds", 0, 6), ("baths", 0, 6), ("park", 0, 4)):
        v, miss = filled(k, lo, hi)
        add(v - med[k], k)
        add(miss, f"{k}_na")
    add((np.nan_to_num(F["park"][ix], nan=1) == 0), "park_0")
    if L["apt"]:
        v, miss = filled("floor", 0, 30)
        add(np.log1p(v) - math.log1p(med["floor"]), "log1p_floor")
        add(miss, "floor_na")
    ab = age_band(F["age"][ix]) if age_override is None else age_override
    for b in range(len(AGE_BANDS)):
        if b != AGE_REF:
            add(ab == b, f"age_{AGE_BANDS[b]}" + (f"_{AGE_BANDS[b + 1]}" if b + 1 < len(AGE_BANDS) else "+"))
    add(ab == -1, "age_na")
    st = F["strat"][ix]
    for s in L["strat"]:
        add(st == s, f"estrato_{s}")
    add(np.isnan(st), "estrato_na")
    D = F["D"][ix]
    for j, c in enumerate(AMEN):
        d = np.where(np.isnan(D[:, j]), L["dmed"][j], D[:, j])
        add(np.log(np.clip(d, 100, 5000)) - math.log(np.clip(L["dmed"][j], 100, 5000)), f"ld_{c}")
    for j, k in enumerate(RISKS):
        add(F["risk"][ix, j], f"risk_{k}")
    for j, k in enumerate(PROJS):
        add(F["proj"][ix, j], f"proj_{k}")
    add(F["approx"][ix], "approx")
    add(F["reno"][ix] if reno_override is None else reno_override, "kw_renovado")
    add(F["remo"][ix] if reno_override is None else 0 * F["remo"][ix], "kw_remodelar")
    add(F["mixto"][ix], "kw_mixto")
    fe = [("comuna", DEFAULTS["lam_comuna"]), ("barrio", DEFAULTS["lam_barrio"])]
    for k, p in fe + ([("cell", DEFAULTS["lam_cell"])] if DEFAULTS["cell_m"] else []):
        pos = {v: j for j, v in enumerate(L[k])}
        oh = np.zeros((len(ix), len(pos)))
        jj = np.array([pos.get(v, -1) for v in F[k][ix]], int)
        ok = jj >= 0
        oh[np.nonzero(ok)[0], jj[ok]] = 1
        cols.append(oh)
        names += [f"{k}:{v}" for v in L[k]]
        pens += [p] * len(pos)
    X = np.column_stack(cols)
    return X, np.array(pens), names, levels


def huber_ridge(X, y, pen, delta=None, iters=30, tol=1e-4):
    """Huber IRLS con ridge: min Σ ρ_δ((y − Xb)/s) + ½ Σ pen·b². -> (b, s, cov, w)."""
    delta = delta or DEFAULTS["delta"]
    w = np.ones(len(y))
    b = np.zeros(X.shape[1])
    s = 1.0
    P = np.diag(pen)
    for _ in range(iters):
        Xw = X * w[:, None]
        A = X.T @ Xw + P
        nb = np.linalg.solve(A, Xw.T @ y)
        r = y - X @ nb
        s = max(1.4826 * float(np.median(np.abs(r - np.median(r)))), 1e-6)
        u = np.abs(r) / (delta * s)
        w = np.where(u <= 1, 1.0, 1.0 / np.maximum(u, 1e-12))
        done = np.max(np.abs(nb - b)) < tol
        b = nb
        if done:
            break
    Xw = X * w[:, None]
    Ai = np.linalg.inv(X.T @ Xw + P)
    cov = s ** 2 * Ai @ (X.T @ (X * (w ** 2)[:, None])) @ Ai  # sándwich aproximado
    return b, s, cov, w


class Model:
    """Un modelo por tipo. fit(F, train_ix) y predict(F, ix) -> log(precio/m²)."""

    def fit(self, F, ix):
        self.parts = {}
        for t in sorted(set(F["type"][ix])):
            tix = ix[F["type"][ix] == t]
            if len(tix) < 30:
                continue
            X, pen, names, lv = design(F, tix)
            b, s, cov, _ = huber_ridge(X, F["y"][tix], pen)
            self.parts[t] = dict(b=b, s=s, cov=cov, names=names, levels=lv, n=len(tix))
        return self

    def predict(self, F, ix, arv=False):
        out = np.full(len(ix), np.nan)
        for t, m in self.parts.items():
            sel = np.nonzero(F["type"][ix] == t)[0]
            if not len(sel):
                continue
            kw = {}
            if arv:  # renovado, sin "para remodelar" y, si es más viejo que la banda de buen estado, en esa banda
                ab = age_band(F["age"][ix[sel]])
                kw = dict(age_override=np.where(ab > AGE_GOOD, AGE_GOOD, ab),
                          reno_override=np.ones(len(sel)))
            X, _, _, _ = design(F, ix[sel], m["levels"], **kw)
            out[sel] = X @ m["b"]
        return out


def crossfit(F, train, pred_ix, folds=5, seed=0):
    """Pliegues aleatorios sobre `train`. -> (P, PA, fold, modelo completo): P[k] / PA[k] = log pred (normal / ARV) de
    todas las `pred_ix` con el modelo que no vio el pliegue k (fila `folds` = modelo completo). Una oferta del pliegue k
    se juzga con P[k] para ella y para sus comparables, así nunca entra al modelo que la juzga."""
    rng = np.random.default_rng(seed)
    n = len(F["y"])
    fold = np.full(n, folds)
    tix = np.nonzero(train)[0]
    fold[tix] = rng.integers(0, folds, len(tix))
    pred_ix = np.asarray(pred_ix, int)
    P, PA = np.full((folds + 1, n), np.nan), np.full((folds + 1, n), np.nan)
    for k in range(folds + 1):
        m = Model().fit(F, tix[fold[tix] != k])
        P[k, pred_ix] = m.predict(F, pred_ix)
        PA[k, pred_ix] = m.predict(F, pred_ix, arv=True)
    return P, PA, fold, m


def group_folds(keys, folds=5):
    """Pliegues espaciales: agrupa claves (comunas) en `folds` grupos de tamaño parecido (codicioso)."""
    u, cnt = np.unique(keys, return_counts=True)
    load = np.zeros(folds)
    g = {}
    for j in np.argsort(-cnt):
        k = int(np.argmin(load))
        g[u[j]] = k
        load[k] += cnt[j]
    return np.array([g[k] for k in keys])


def amenity_table(model, old_w, floor=3, total=100):
    """Elasticidad de cada distancia (por tipo y ponderada por n), t, peso implícito ∝ −β (solo si β < 0 y |t| ≥ 2;
    piso `floor` para las de signo correcto pero no significativas; 0 si el signo es contrario y significativo)."""
    rows = []
    ntot = sum(m["n"] for m in model.parts.values())
    for c in AMEN:
        bs, vs = 0.0, 0.0
        for m in model.parts.values():
            j = m["names"].index(f"ld_{c}")
            f = m["n"] / ntot
            bs += f * m["b"][j]
            vs += f * f * m["cov"][j, j]
        t = bs / math.sqrt(vs) if vs > 0 else 0.0
        rows.append(dict(cat=c, beta=bs, t=t, old=old_w.get(c, 0)))
    raw = {r["cat"]: (-r["beta"] if r["beta"] < 0 and r["t"] <= -2 else None) for r in rows}
    sig = {k: v for k, v in raw.items() if v is not None}
    s = sum(sig.values()) or 1.0
    n_floor = sum(1 for r in rows if raw[r["cat"]] is None and not (r["beta"] > 0 and r["t"] >= 2))
    budget = total - floor * n_floor
    for r in rows:
        if raw[r["cat"]] is not None:
            r["new"] = max(floor, int(round(budget * raw[r["cat"]] / s)))
        elif r["beta"] > 0 and r["t"] >= 2:
            r["new"] = 0
        else:
            r["new"] = floor
    return rows


def metrics(pred_pm, pm):
    ape = np.abs(pred_pm - pm) / pm
    return dict(n=int(len(ape)), mape=float(np.mean(ape)), mdape=float(np.median(ape)),
                within10=float(np.mean(ape <= 0.10)), within20=float(np.mean(ape <= 0.20)))


def summary(model, amen_rows=None):
    """Resumen chico para data/hedonic.json: coeficientes no-FE con t, dispersión de efectos de barrio."""
    out = {}
    for t, m in model.parts.items():
        se = np.sqrt(np.maximum(np.diag(m["cov"]), 0))
        fe = [j for j, nm in enumerate(m["names"]) if nm.startswith(("barrio:", "comuna:", "cell:"))]
        bar = np.array([m["b"][j] for j in fe if m["names"][j].startswith("barrio:")])
        out[t] = dict(n=m["n"], scale=round(m["s"], 4),
                      coef={nm: [round(float(m["b"][j]), 4), round(float(m["b"][j] / se[j]), 1) if se[j] else None]
                            for j, nm in enumerate(m["names"]) if j not in set(fe)},
                      barrio_fe=dict(n=len(bar), sd=round(float(bar.std()), 3) if len(bar) else None))
    if amen_rows:
        out["amenity_weights"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                                  for r in amen_rows]
    return out


def validate(F, train, res, pm, skey, full, C, cfg, out_json="data/hedonic.json"):
    """Compara kNN (leave-self-out) vs hedónico fuera de muestra: (a) pliegues aleatorios = como corre en producción;
    (b) pliegues espaciales por comuna (leave-comuna-group-out). También ARV P75 vs hedónico."""
    ev = np.array(sorted(res))
    y = pm[ev]
    knn = np.array([res[i]["exp_knn"] for i in ev])
    hed = np.array([res[i]["exp_hed"] or np.nan for i in ev])
    ok = np.isfinite(hed)
    exact = F["approx"][ev] == 0
    R = {}

    def block(name, h, sel):
        b = {"knn": metrics(knn[sel], y[sel]), "hedonic": metrics(h[sel], y[sel])}
        for w in (0.3, 0.4, 0.5, 0.7):
            b[f"blend_{w}"] = metrics(knn[sel] ** (1 - w) * h[sel] ** w, y[sel])
        R[name] = b

    block("random_all", hed, ok)
    block("random_exact", hed, ok & exact)
    # espacial: comunas agrupadas en 5 pliegues; la comuna evaluada no aporta ni efectos de barrio ni de comuna
    fold = group_folds(skey, C["hedonic"]["folds"])
    tix = np.nonzero(train)[0]
    hs = np.full(len(ev), np.nan)
    for k in range(C["hedonic"]["folds"]):
        m = Model().fit(F, tix[fold[tix] != k])
        sel = np.nonzero(fold[ev] == k)[0]
        hs[sel] = np.exp(m.predict(F, ev[sel]))
    oks = np.isfinite(hs)
    block("spatial_all", hs, oks)
    block("spatial_exact", hs, oks & exact)
    for t in ("apartamento", "casa"):
        block(f"random_{t}", hed, ok & (F["type"][ev] == t))
    print("[hedonic] validación fuera de muestra (APE del precio/m²):")
    for name, b in R.items():
        print(f"  {name:<18} n={b['knn']['n']:>5}  " + "  ".join(
            f"{k}: MAPE {v['mape']:.3f} MdAPE {v['mdape']:.3f}" for k, v in b.items() if k in ("knn", "hedonic")))
        print(" " * 28 + "  ".join(f"{k} {v['mape']:.3f}/{v['mdape']:.3f}" for k, v in b.items() if k not in ("knn", "hedonic")))
    # ARV: P75 de comparables vs hedónico (renovado + banda de buen estado), relativo al expected, antes del tope
    cap = 1 + C["arv"]["cap_vs_expected"]
    a75 = np.array([res[i]["arv_p75"] / res[i]["exp"] for i in ev])
    ah = np.array([(res[i]["arv_hed"] or np.nan) / res[i]["exp"] for i in ev])
    q = [10, 25, 50, 75, 90]
    R["arv"] = dict(p75_ratio=np.nanpercentile(a75, q).round(3).tolist(), hed_ratio=np.nanpercentile(ah, q).round(3).tolist(),
                    p75_capped=float(np.mean(a75 > cap)), hed_capped=float(np.nanmean(ah > cap)))
    print("[hedonic] ARV/expected P10..P90  P75 comps:", R["arv"]["p75_ratio"], f"(tope {R['arv']['p75_capped']:.0%})",
          "| hedónico:", R["arv"]["hed_ratio"], f"(tope {R['arv']['hed_capped']:.0%})")
    # pesos de amenidades: la celda de 300 m absorbe casi toda la variación de las distancias, así que la elasticidad
    # se estima sin celdas (efecto de barrio: compara dentro del barrio) y, como referencia, solo con comuna
    old_w = (cfg.get("score") or {}).get("amenity_weights") or {}
    tables = {}
    for spec, upd in (("barrio", dict(cell_m=0)), ("comuna", dict(cell_m=0, lam_barrio=1e9))):
        keep = dict(DEFAULTS)
        DEFAULTS.update(upd)
        tables[spec] = amenity_table(Model().fit(F, tix), old_w)
        DEFAULTS.clear()
        DEFAULTS.update(keep)
    amen = tables["barrio"]
    R["amenity_comuna_only"] = [dict(cat=r["cat"], beta=round(r["beta"], 4), t=round(r["t"], 1)) for r in tables["comuna"]]
    print("[hedonic] amenidades: categoría  β barrio-FE (t)  β solo comuna (t)  peso viejo -> nuevo")
    for r, rc in zip(amen, tables["comuna"]):
        print(f"  {r['cat']:<14} {r['beta']:+.4f} ({r['t']:+.1f})  {rc['beta']:+.4f} ({rc['t']:+.1f})  {r['old']:>3} -> {r['new']}")
    js = dict(validation=R, model=summary(full, amen))
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=1)
    print(f"[hedonic] -> {out_json}")
    return js


if __name__ == "__main__":
    from scraper import db, score
    conn = db.connect()
    db.init(conn)
    score.run(conn, validate=True)

"""Metrocuadrado: API JSON interna (proxy /api/rest-search del propio front). Cali completo, sin byPoint."""
import re
import unicodedata

BASE = "https://www.metrocuadrado.com"
API = f"{BASE}/api/rest-search/search"
OPS = {"sale": "venta", "rent": "arriendo"}
PRICE_FIELD = {"sale": "mvalorventa", "rent": "mvalorarriendo"}
PAGE = 50  # la API devuelve 0 resultados con size pequeño
# El orden por defecto (sortField=morden) es aleatorio entre llamadas, lo que rompe la paginación (repetidos y
# huecos). Ordenar por precio es estable. No existe orden por fecha (campos de fecha se ignoran), así que no hay
# "más reciente primero": incremental recorre todo igual que full (ver fetch).
RECENT_SORT = None  # {"sortField": ..., "sortOrder": "desc"} si algún día aparece uno real
# claves de `featured` que ya mapeamos a campos propios o que no son características
SKIP_FEATURES = {"area", "areaConstruida", "areaLote", "nroBanos", "nroCuartos", "nroGarajes", "nroPiso",
                 "tiempoConstruido", "nroClosets", "nroAscensores", "contactoWhatsApp", "negociarPrecio", "nroApto", "nroInterior"}


def fetch(cfg, http, mode="full", known=frozenset()):
    sc = cfg["scraping"]
    max_pages = sc.get("max_pages")
    stop_after = sc["incremental_stop_after_known_pages"]
    strata = ",".join(str(n) for n in range(cfg["min_stratum"], cfg["max_stratum"] + 1))
    kws = [norm(k) for k in cfg.get("exclude_keywords", [])]
    seen, out = set(), []
    for op in cfg["operations"]:
        for ptype in cfg["property_types"]:
            sort = RECENT_SORT if mode == "incremental" and RECENT_SORT else \
                {"sortField": PRICE_FIELD[op], "sortOrder": "asc"}
            total, known_pages = None, 0
            for page in range(max_pages or 10**6):
                params = {"size": PAGE, "from": page * PAGE, "city": cfg["city"], "stratumList": strata,
                          "realEstateTypeList": ptype, "realEstateBusinessList": OPS[op], **sort}
                d = http.get_json(API, params=params)
                total = d["totalHits"] if total is None else total  # con from fuera de rango totalHits cambia
                results = d["results"]
                for r in results:
                    if r["midinmueble"] in seen:  # vienen destacados repetidos
                        continue
                    seen.add(r["midinmueble"])
                    if r["link"].startswith("/proyecto/"):  # proyectos sobre planos / nuevos
                        continue
                    rec = parse(r, op, ptype)
                    if rec and not any(k in norm(f"{rec['title']} {rec['description']}") for k in kws):
                        out.append(rec)
                if not results or (page + 1) * PAGE >= total:
                    break
                if mode == "incremental" and RECENT_SORT:
                    known_pages = known_pages + 1 if all(r["midinmueble"] in known for r in results) else 0
                    if known_pages >= stop_after:
                        break
    return out


def available(url, http):
    """Revisa la página pública del anuncio (/inmueble/...): publicationStatus "N" o "no está disponible" = retirado."""
    r = http.fetch(url)
    if r.status_code == 404:
        return False
    h = r.text.replace('\\"', '"')
    return '"publicationStatus":"N"' not in h and "no está disponible" not in h


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def num_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def age_years(text):
    """'Entre 5 y 10 años' -> 7.5; 'Más de 20 años' -> 25; 'Menos de 1 año'/'estrenar' -> 0.5/0."""
    t = norm(text)
    if m := re.search(r"entre (\d+) y (\d+)", t):
        return (int(m[1]) + int(m[2])) / 2
    if m := re.search(r"mas de (\d+)", t):
        return int(m[1]) + 5.0
    if m := re.search(r"menos de (\d+)", t):
        return int(m[1]) / 2
    return 0.0 if "estrenar" in t else None


def photos(r):
    """Galería completa en 800 px (_x). La carpeta sale del imageLink."""
    link = r.get("imageLink") or ""
    if not r.get("mgaleriainmueble"):
        return [link] if link else []
    folder = link.rsplit("/", 1)[0]
    return [f"{folder}/{g}_x.jpg" for g in r["mgaleriainmueble"]]


def parse(r, op, ptype):
    price = num(r.get(PRICE_FIELD[op]))
    if not price:
        return None
    feats = {}
    for f in r.get("featured") or []:
        k, _, v = f.partition(":")
        feats[k.strip()] = v.strip()
    loc = r.get("localizacion") or {}
    readable = []
    for k, v in feats.items():
        if k in SKIP_FEATURES or not v or v.upper() == "N":
            continue
        k = re.sub(r"(_o|-cb)$", "", k)
        readable.append(k if v.upper() == "S" else f"{k}: {v}")
    return {
        "source": "metrocuadrado",
        "source_id": r["midinmueble"],
        "url": BASE + r["link"],
        "operation": op,
        "property_type": ptype,
        "title": r.get("title"),
        "description": r.get("comment") or "",
        "price": price,
        "admin_fee": num((r.get("data") or {}).get("mvaloradministracion")) or None,
        "area_m2": num_f(r.get("mareac") or r.get("marea")),
        "area_lot_m2": num_f(feats.get("areaLote")),
        "bedrooms": num(r.get("mnrocuartos")),
        "bathrooms": num(r.get("mnrobanos")),
        "parking": num(r.get("mnrogarajes")),
        "floor": num(feats.get("nroPiso")),
        "age_years": age_years(feats.get("tiempoConstruido")),
        "stratum": num(r.get("estrato")),
        "neighborhood": None if norm(r.get("mbarrio")).strip() in ("", "na", "nn") else r["mbarrio"].title(),
        "lat": num_f(loc.get("lat")) or None,  # sin ubicación M2 manda (0, 0)
        "lng": num_f(loc.get("lon")) or None,
        "approx_location": r.get("ubicacionaproximada") == "S",
        "photos": photos(r),
        "features": readable,
        "published_at": None,  # la API no trae fecha
    }


if __name__ == "__main__":
    import json
    import yaml
    from scraper.net import Http

    cfg = yaml.safe_load(open("config.yaml"))
    cfg["scraping"]["max_pages"] = 1
    http = Http(cfg["scraping"])
    keys = {"source", "source_id", "url", "operation", "property_type", "title", "description", "price",
            "admin_fee", "area_m2", "area_lot_m2", "bedrooms", "bathrooms", "parking", "floor", "age_years",
            "stratum", "neighborhood", "lat", "lng", "approx_location", "photos", "features", "published_at"}
    recs = []
    for op in cfg["operations"]:
        for pt in cfg["property_types"]:
            c = {**cfg, "operations": [op], "property_types": [pt]}
            rs = fetch(c, http)
            print(op, pt, len(rs))
            assert rs and all(set(r) == keys and r["url"].startswith("https://") and isinstance(r["price"], int)
                              and isinstance(r["photos"], list) and isinstance(r["features"], list)
                              and r["operation"] == op and r["property_type"] == pt
                              and not r["url"].startswith(BASE + "/proyecto/") for r in rs)
            recs += rs
    assert len({r["source_id"] for r in recs}) == len(recs)
    print(json.dumps(recs[0], ensure_ascii=False, indent=1))
    print("ok")

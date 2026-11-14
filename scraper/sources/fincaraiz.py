"""Finca Raíz: JSON embebido en <script id="__NEXT_DATA__"> (ver docs/sources.md).

Una búsqueda = operación x tipo x estrato (un estrato por URL). El portal solo entrega ~6120 resultados por
búsqueda (la página 293 de 294 ya viene vacía), así que dividir por estrato mantiene cada búsqueda bajo el tope.
Siempre se ordena por más recientes (`ordenListado=3`): es estable durante una corrida larga y sirve al modo incremental.
"""
import json
import re
import unicodedata

BASE = "https://www.fincaraiz.com.co"
OPS = {"rent": "arriendo", "sale": "venta"}
TYPES = {"apartamento": "apartamentos", "casa": "casas"}
RECENT_FIRST = "ordenListado=3"
# technicalSheet "Antigüedad" -> años (punto medio del rango)
AGES = {"menor a 1 año": 0.5, "1 a 8 años": 4.5, "9 a 15 años": 12, "16 a 30 años": 23, "más de 30 años": 35}
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch(cfg, http, mode="full", known=frozenset()):
    sc = cfg["scraping"]
    max_pages = sc.get("max_pages")
    stop_after = sc["incremental_stop_after_known_pages"]
    exclude = [norm(k) for k in cfg.get("exclude_keywords") or []]
    seen, out = set(), []
    for op in cfg["operations"]:
        for ptype in cfg["property_types"]:
            for stratum in range(cfg["min_stratum"], cfg["max_stratum"] + 1):
                known_pages = 0
                page = 1
                while max_pages is None or page <= max_pages:
                    sf = search_page(http, op, ptype, stratum, page)
                    new_on_page = False
                    for i in sf["data"]:
                        if is_project(i) or not (i.get("price") or {}).get("amount"):
                            continue
                        rec = parse(i, op, ptype)
                        if any(k in norm(rec["title"] + " " + (rec["description"] or "")) for k in exclude):
                            continue
                        if rec["source_id"] not in known:
                            new_on_page = True
                        if rec["source_id"] not in seen:  # destacados repetidos
                            seen.add(rec["source_id"])
                            out.append(rec)
                    if not sf["data"] or not sf["paginatorInfo"]["hasMorePages"]:
                        break
                    known_pages = 0 if new_on_page else known_pages + 1
                    if mode == "incremental" and known_pages >= stop_after:
                        break
                    page += 1
    return out


def search_page(http, op, ptype, stratum, page):
    path = f"{BASE}/{OPS[op]}/{TYPES[ptype]}/cali/valle-del-cauca" + (f"/pagina{page}" if page > 1 else "")
    html = http.get(f"{path}?stratum[]={stratum}&{RECENT_FIRST}")
    m = NEXT_DATA.search(html)
    if not m:
        raise RuntimeError(f"sin __NEXT_DATA__ en {path}")
    return json.loads(m.group(1))["props"]["pageProps"]["fetchResult"]["searchFast"]


def available(url, http):
    """Revisa la página del anuncio: 404 o data.active == False (con inactiveSimilarInfo) = retirado."""
    r = http.fetch(url)
    if r.status_code == 404:
        return False
    m = NEXT_DATA.search(r.text)
    if not m:
        return True  # ante la duda no se marca
    pp = json.loads(m.group(1))["props"]["pageProps"]
    data = pp.get("data") if isinstance(pp.get("data"), dict) else {}
    return not (data.get("active") is False or pp.get("inactiveSimilarInfo"))


def is_project(i):
    return bool(i.get("isProject") or i.get("isProjectUnit") or i.get("project")
                or i["link"].startswith("/proyectos-vivienda"))


def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def parse(i, op, ptype):
    sheet = {t["field"]: t["value"] for t in i.get("technicalSheet") or []}
    loc = i.get("locations") or {}
    main = loc.get("location_main") or {}
    hoods = loc.get("neighbourhood") or []
    hood = main.get("name") if main.get("location_type") == "neighbourhood" else (hoods[0]["name"] if hoods else None)
    photos = [im["image"] for im in i.get("images") or [] if im.get("image")] or ([i["img"]] if i.get("img") else [])
    return {
        "source": "fincaraiz",
        "source_id": str(i["id"]),
        "url": BASE + i["link"],
        "operation": op,
        "property_type": ptype,
        "title": i["title"],
        "description": i.get("description") or "",
        "price": int(i["price"]["amount"]),
        "admin_fee": num((i.get("commonExpenses") or {}).get("amount")) or None,
        "area_m2": flt(i.get("m2Built")) or flt(i.get("m2")),
        "area_lot_m2": flt(i.get("m2Terrain")) if ptype == "casa" else None,
        "bedrooms": num(i.get("bedrooms")),
        "bathrooms": num(i.get("bathrooms")),
        "parking": num(i.get("garage")),
        "floor": num(i.get("floor")) or None,
        "age_years": AGES.get((sheet.get("constructionYear") or "").strip().lower()),
        "stratum": num(i.get("stratum")),
        "neighborhood": hood,
        "lat": flt(i.get("latitude")),
        "lng": flt(i.get("longitude")),
        "approx_location": bool(i.get("pointType")),
        "photos": photos,
        "features": [f["name"].strip() for f in i.get("facilities") or [] if f.get("name")],
        "published_at": i.get("created_at"),
    }


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def flt(v):
    try:
        return float(v) or None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    import sys
    import yaml

    sys.path.insert(0, ".")
    from scraper.net import Http

    cfg = yaml.safe_load(open("config.yaml"))
    cfg["scraping"]["max_pages"] = 1
    cfg["min_stratum"] = cfg["max_stratum"] = 4  # un estrato basta para el chequeo
    http = Http(cfg["scraping"])
    keys = {"source", "source_id", "url", "operation", "property_type", "title", "description", "price", "admin_fee",
            "area_m2", "area_lot_m2", "bedrooms", "bathrooms", "parking", "floor", "age_years", "stratum",
            "neighborhood", "lat", "lng", "approx_location", "photos", "features", "published_at"}
    recs = fetch(cfg, http)
    for op in cfg["operations"]:
        for t in cfg["property_types"]:
            n = [r for r in recs if r["operation"] == op and r["property_type"] == t]
            assert n, (op, t)
    for r in recs:
        assert set(r) == keys, set(r) ^ keys
        assert r["source"] == "fincaraiz" and r["url"].startswith("https://") and isinstance(r["price"], int)
        assert isinstance(r["source_id"], str) and isinstance(r["photos"], list) and isinstance(r["features"], list)
        assert isinstance(r["approx_location"], bool) and isinstance(r["description"], str)
        assert r["area_m2"] is None or isinstance(r["area_m2"], float)
    print(len(recs), "registros")
    print("ok")

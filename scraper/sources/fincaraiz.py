import json
import re

BASE = "https://www.fincaraiz.com.co"
OPS = {"rent": "arriendo", "sale": "venta"}
TYPES = {"apartamento": "apartamentos", "casa": "casas"}
# technicalSheet "Antigüedad" -> (min, max) años
AGES = {"menos de 1 año": (0, 1), "1 a 8 años": (1, 8), "9 a 15 años": (9, 15),
        "16 a 30 años": (16, 30), "más de 30 años": (30, None)}
LAUNDRY_FACILITIES = {"zona de lavandería", "terraza lavadero"}


def search_urls(cfg):
    strata = "&".join(f"stratum[]={s}" for s in range(cfg["min_stratum"], 7))
    for op, op_slug in OPS.items():
        for ptype in cfg["property_types"]:
            yield op, ptype, f"{BASE}/{op_slug}/{TYPES[ptype]}/zona-norte/cali/{cfg['min_bedrooms']}-o-mas-habitaciones", strata


def fetch(cfg, http):
    out = []
    for op, ptype, url, qs in search_urls(cfg):
        for page in range(1, cfg["scraping"]["max_pages_per_search"] + 1):
            html = http.get(url + (f"/pagina{page}" if page > 1 else "") + "?" + qs)
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if not m:
                raise RuntimeError(f"sin __NEXT_DATA__ en {url} página {page}")
            sf = json.loads(m.group(1))["props"]["pageProps"]["fetchResult"]["searchFast"]
            out += [parse(i, op, ptype) for i in sf["data"]]
            if not sf["paginatorInfo"]["hasMorePages"]:
                break
    return out


def available(url, http):
    """Revisa la página del anuncio: 404 o data.active == False (con inactiveSimilarInfo) = retirado."""
    r = http.fetch(url)
    if r.status_code == 404:
        return False
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return True  # ante la duda no se marca
    pp = json.loads(m.group(1))["props"]["pageProps"]
    data = pp.get("data") if isinstance(pp.get("data"), dict) else {}
    return not (data.get("active") is False or pp.get("inactiveSimilarInfo"))


def parse(i, op, ptype):
    sheet = {t["field"]: t["value"] for t in i.get("technicalSheet") or []}
    age = AGES.get((sheet.get("constructionYear") or "").strip().lower())
    hoods = i["locations"].get("neighbourhood") or []
    facilities = [f["name"].strip() for f in i.get("facilities") or []]
    return {
        "source": "fincaraiz",
        "source_id": str(i["id"]),
        "url": BASE + i["link"],
        "operation": op,
        "property_type": ptype,
        "title": i["title"],
        "price": i["price"]["amount"],
        "admin_fee": i["commonExpenses"]["amount"] or None,
        # admin_included = canon + administración
        "price_total": i["price"]["admin_included"] or i["price"]["amount"],
        "admin_included": bool(i.get("include_administration")),  # el canon ya incluye la admin
        "bedrooms": num(i.get("bedrooms")),
        "bathrooms": num(i.get("bathrooms")),
        "parking": num(i.get("garage")),
        "area_m2": num(i.get("m2Built") or i.get("m2")),
        "age_min": age[0] if age else None,
        "age_max": age[1] if age else None,
        "stratum": num(i.get("stratum")),
        "neighborhood": hoods[-1]["name"] if hoods else None,
        "lat": float(i["latitude"]) if i.get("latitude") else None,
        "lng": float(i["longitude"]) if i.get("longitude") else None,
        "laundry_field": True if LAUNDRY_FACILITIES & {f.lower() for f in facilities} else None,
        "text": f"{i['title']}\n{i.get('description') or ''}\n{' '.join(facilities)}",
        "description": f"{i['title']}\n{i.get('description') or ''}",
        "thumbnail": i.get("img"),
        "photos": [im["image"] for im in i.get("images") or [] if im.get("image")][:8] or ([i["img"]] if i.get("img") else []),
        "published_at": i.get("created_at"),  # YYYY-MM-DD
    }


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

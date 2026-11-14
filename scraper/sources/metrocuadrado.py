BASE = "https://www.metrocuadrado.com"
OPS = {"rent": "arriendo", "sale": "venta"}
AGES = {"entre 0 y 5 años": (0, 5), "entre 5 y 10 años": (5, 10),
        "entre 10 y 20 años": (10, 20), "más de 20 años": (20, None)}
# ponytail: búsqueda por radio alrededor del norte; el polígono recorta después
CENTER = {"latitude": 3.477, "longitude": -76.527, "distance": 4}
PAGE = 50  # la API devuelve 0 resultados con size pequeño


def fetch(cfg, http):
    # el front (desde nov-2026) consulta el proxy /api/rest-search, que añade la key en su servidor
    seen, out = set(), []
    for op, op_slug in OPS.items():
        for ptype in cfg["property_types"]:
            total = None
            for page in range(cfg["scraping"]["max_pages_per_search"]):
                params = {
                    "size": PAGE, "from": page * PAGE, "city": "cali",
                    "realEstateTypeList": ptype, "realEstateBusinessList": op_slug,
                    "roomList": ",".join(str(n) for n in range(cfg["min_bedrooms"], 6)),
                    "stratumList": ",".join(str(n) for n in range(cfg["min_stratum"], 7)),
                    "byPoint": "true", **CENTER,
                }
                d = http.get_json(f"{BASE}/api/rest-search/search", params=params)
                total = d["totalHits"] if total is None else total
                for r in d["results"]:
                    if r["midinmueble"] not in seen:  # vienen destacados repetidos
                        seen.add(r["midinmueble"])
                        out.append(parse(r, op, ptype))
                if (page + 1) * PAGE >= total:
                    break
    return out


def available(url, http):
    """Revisa la página del anuncio: publicationStatus "N" o "no está disponible" = retirado."""
    r = http.fetch(url)
    if r.status_code == 404:
        return False
    h = r.text.replace('\\"', '"')
    return '"publicationStatus":"N"' not in h and "no está disponible" not in h


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse(r, op, ptype):
    feats = dict(f.strip().split(":", 1) for f in r.get("featured") or [] if ":" in f)
    age = AGES.get((feats.get("tiempoConstruido") or "").strip().lower())
    loc = r.get("localizacion") or {}
    price = r["mvalorarriendo"] if op == "rent" else r["mvalorventa"]
    admin = num((r.get("data") or {}).get("mvaloradministracion")) or None
    laundry = feats.get("conZonaLavanderia") == "S" or feats.get("conZonaLavanderia-cb") == "S"
    return {
        "source": "metrocuadrado",
        "source_id": r["midinmueble"],
        "url": BASE + r["link"],
        "operation": op,
        "property_type": ptype,
        "title": r["title"],
        "price": price,
        "admin_fee": admin,
        "price_total": price + (admin or 0),
        "admin_included": False,
        "bedrooms": num(r.get("mnrocuartos")),
        "bathrooms": num(r.get("mnrobanos")),
        "parking": num(r.get("mnrogarajes")),
        "area_m2": num(r.get("mareac") or r.get("marea")),
        "age_min": age[0] if age else None,
        "age_max": age[1] if age else None,
        "stratum": r.get("estrato"),
        "neighborhood": (r.get("mbarrio") or "").title() or None,
        "lat": num_f(loc.get("lat")),
        "lng": num_f(loc.get("lon")),
        "approx_location": r.get("ubicacionaproximada") == "S",
        "laundry_field": True if laundry else None,
        "description": f"{r['title']}\n{r.get('comment') or ''}",
        "text": f"{r['title']}\n{r.get('comment') or ''}\n{' '.join(k for k, v in feats.items() if v.strip().upper() != 'N')}",
        "thumbnail": r.get("imageLink"),
        "photos": photos(r),
    }


def photos(r):
    """Hasta 8 fotos de la galería en 800 px (_x). La carpeta sale del imageLink."""
    link = r.get("imageLink") or ""
    if not link or not r.get("mgaleriainmueble"):
        return [link] if link else []
    folder = link.rsplit("/", 1)[0]
    return [f"{folder}/{g}_x.jpg" for g in r["mgaleriainmueble"][:8]]


def num_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

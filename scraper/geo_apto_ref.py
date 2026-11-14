import json
import math
import time
from collections import defaultdict

from scoring import norm

GEOCACHE = "data/geocache.json"


def load_polygon(path):
    return json.load(open(path))["features"][0]["geometry"]["coordinates"][0]


def in_polygon(lng, lat, poly):
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def meters(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def mark_precision(listings):
    """exact salvo: sin coords, marcada aproximada por el portal, o coordenada genérica
    compartida por ≥3 barrios distintos (los portales usan puntos por defecto)."""
    hoods = defaultdict(set)
    for l in listings:
        if l["lat"] and l["lng"]:
            hoods[(round(l["lat"], 5), round(l["lng"], 5))].add(norm(l["neighborhood"]))
    for l in listings:
        ok = l["lat"] and l["lng"] and not l.pop("approx_location", False)
        if ok and len(hoods[(round(l["lat"], 5), round(l["lng"], 5))]) >= 3:
            ok = False
        l["geo_precision"] = "exact" if ok else "neighborhood"


class Geocoder:
    """Nominatim por barrio, con caché local y máximo 1 request/s."""

    def __init__(self, http):
        self.http = http
        try:
            self.cache = json.load(open(GEOCACHE))
        except FileNotFoundError:
            self.cache = {}

    def locate(self, neighborhood, **kw):
        key = norm(neighborhood).strip()
        if not key:
            return None
        if key not in self.cache:
            time.sleep(1.1)
            r = self.http.get_json("https://nominatim.openstreetmap.org/search", params={
                "q": f"{neighborhood}, Cali, Valle del Cauca, Colombia", "format": "json", "limit": 1,
            }, polite=False, **kw)
            self.cache[key] = [float(r[0]["lat"]), float(r[0]["lon"])] if r else None
        return self.cache[key]

    def save(self):
        json.dump(self.cache, open(GEOCACHE, "w"), ensure_ascii=False, indent=1, sort_keys=True)

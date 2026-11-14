from geo import meters


def is_dup(a, b):
    if a["operation"] != b["operation"] or a["bedrooms"] != b["bedrooms"]:
        return False
    if not (a["lat"] and b["lat"]) or meters(a, b) >= 80:
        return False
    if abs(a["price"] - b["price"]) / max(a["price"], b["price"]) >= 0.05:
        return False
    if a.get("area_m2") and b.get("area_m2"):
        return abs(a["area_m2"] - b["area_m2"]) / max(a["area_m2"], b["area_m2"]) < 0.10
    return True


def dedupe(listings):
    """Marca duplicate_of apuntando a la primera vista. ponytail: O(n²), sobra para cientos."""
    active = sorted((l for l in listings if l["status"] == "active"), key=lambda l: (l["first_seen"], l["id"]))
    for l in active:
        l["duplicate_of"] = None
    for i, b in enumerate(active):
        for a in active[:i]:
            if a["duplicate_of"] is None and is_dup(a, b):
                b["duplicate_of"] = a["id"]
                break


if __name__ == "__main__":
    base = dict(operation="rent", bedrooms=3, lat=3.48, lng=-76.52, price=3_000_000, area_m2=90, status="active")
    a = dict(base, id="fr-1", first_seen="1")
    b = dict(base, id="m2-1", first_seen="2", lat=3.4805, price=3_100_000)  # ~55 m, +3%
    c = dict(base, id="m2-2", first_seen="3", price=3_400_000)  # +13%
    d = dict(base, id="m2-3", first_seen="4", lat=3.4815)  # ~165 m
    dedupe([a, b, c, d])
    assert (a["duplicate_of"], b["duplicate_of"], c["duplicate_of"], d["duplicate_of"]) == (None, "fr-1", None, None)
    print("ok")

#!/usr/bin/env python3
"""Copia y simplifica las capas opcionales del mapa a web/data/layers/ (PLAN RF6).

    python3 scripts/copy_web_layers.py

Entradas: data/layers/*.geojson (ver scripts/fetch_layers.py). Salidas livianas
(coordenadas a 5 decimales, sin puntos repetidos, solo las propiedades útiles).
El paso de export (`python -m scraper.export`) puede llamar a main().
Solo stdlib. Presupuesto total: < 1,5 MB.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "layers"
OUT = ROOT / "web" / "data" / "layers"
BUDGET = 1_500_000

# salida -> (archivo de origen, filtro de feature, propiedades a conservar)
LAYERS = {
    "inundacion_fluvial": ("inundacion_fluvial", None, ["tipo_amena"]),
    "inundacion_pluvial": ("inundacion_pluvial", None, ["tipo_amena"]),
    "inundacion_no_mitigable": ("inundacion_no_mitigable", None, ["tipo_amena"]),
    "tratamientos_renovacion": ("tratamientos", lambda p: "RENOVACION" in (p.get("tratamient") or "").upper(), ["tratamient"]),
    "troncales_proyectadas": ("troncales_proyectadas", None, ["nombre"]),
    "corredor_ferreo": ("corredor_ferreo", None, ["tramo"]),
}


def simplify(coords, nd):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    out = []
    for c in coords:
        s = simplify(c, nd)
        if isinstance(s[0], (int, float)):
            if out and out[-1] == s:
                continue
        out.append(s)
    return out


def drop_tiny_rings(geom):
    """Quita anillos de polígono con < 4 puntos tras redondear."""
    if geom["type"] == "Polygon":
        rings = [r for r in geom["coordinates"] if len(r) >= 4]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if geom["type"] == "MultiPolygon":
        polys = [[r for r in p if len(r) >= 4] for p in geom["coordinates"]]
        polys = [p for p in polys if p]
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None
    return geom


def convert(src_name, keep, props, nd=5):
    data = json.loads((SRC / f"{src_name}.geojson").read_text(encoding="utf-8"))
    feats = []
    for f in data["features"]:
        if keep and not keep(f.get("properties") or {}):
            continue
        g = f.get("geometry")
        if not g:
            continue
        g = drop_tiny_rings({"type": g["type"], "coordinates": simplify(g["coordinates"], nd)})
        if g:
            feats.append({"type": "Feature", "properties": {k: (f.get("properties") or {}).get(k) for k in props}, "geometry": g})
    return {"type": "FeatureCollection", "features": feats}


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, (src, keep, props) in LAYERS.items():
        nd = 5
        while True:
            fc = convert(src, keep, props, nd)
            size = len(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
            if size < 450_000 or nd <= 4:
                break
            nd -= 1
        total += write(OUT / f"{name}.geojson", fc)
        print(f"{name}: {len(fc['features'])} features, {size / 1e3:.0f} KB (decimales={nd})")
    print(f"total capas: {total / 1e6:.2f} MB")
    if total > BUDGET:
        raise SystemExit(f"supera el presupuesto de {BUDGET / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

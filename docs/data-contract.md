# Contratos de datos

Todos los módulos se ejecutan desde la raíz del repo como `python -m scraper.<mod>` con `.venv/bin/python` (Python 3.12).

## 1. Fuentes → `scraper/sources/<name>.py`

```python
def fetch(cfg: dict, http: scraper.net.Http, mode: str = "full", known: set[str] = frozenset()) -> list[dict]
def available(url: str, http) -> bool   # la página del anuncio sigue activa
```

- `mode="full"`: recorre todo (Cali, operaciones `cfg["operations"]`, tipos `cfg["property_types"]`, estratos `min_stratum..max_stratum`).
- `mode="incremental"`: ordenado por más reciente si el portal lo permite; corta una búsqueda después de `cfg["scraping"]["incremental_stop_after_known_pages"]` páginas seguidas sin ningún `source_id` que no esté en `known` (un set de `source_id` de esa fuente).
- Excluye los proyectos sobre planos y nuevos (M2: `link` empieza por `/proyecto/`; FR: el flag de proyecto o desarrollo que exista).
- Ignora los duplicados dentro de la misma corrida (destacados repetidos).

Cada registro (claves exactas; `None` si no hay dato):

| clave | tipo | nota |
|---|---|---|
| source | `"fincaraiz"` \| `"metrocuadrado"` | |
| source_id | str | |
| url | str | absoluta |
| operation | `"sale"` \| `"rent"` | |
| property_type | `"apartamento"` \| `"casa"` | |
| title, description | str | descripción completa |
| price | int COP | |
| admin_fee | int \| None | |
| area_m2 | float \| None | área **construida** preferida (FR `m2Built`, M2 `mareac`) |
| area_lot_m2 | float \| None | área de lote/total si el portal la da aparte |
| bedrooms, bathrooms, parking | int \| None | |
| floor | int \| None | |
| age_years | float \| None | punto medio del rango si viene en rango ("10 a 20" → 15; "más de 20" → 25) |
| stratum | int \| None | |
| neighborhood | str \| None | el barrio más específico, tal como viene del portal |
| lat, lng | float \| None | |
| approx_location | bool | el portal la marca como aproximada |
| photos | list[str] | todas las URLs disponibles en el listado |
| features | list[str] | facilidades o características legibles |
| published_at | `"YYYY-MM-DD"` \| None | |

## 2. SQLite `data/sic.db` (ver PLAN.md §5.3)

Lo dueña `scraper/db.py`. `id = "fr-<source_id>"` \| `"m2-<source_id>"`.

## 3. Export web: `web/data/`

### `listings.json`: ventas activas, canónicas (una por grupo de dedupe)
```json
{
  "generated_at": "2026-11-14T20:00:00Z",
  "listings": [{
    "id": "fr-123", "t": "a",            // a = apartamento, c = casa
    "p": 320000000, "a": 78.0, "pm": 4102564,
    "b": 3, "ba": 2, "pk": 1, "e": 4, "age": 15,
    "br": "Ciudad Jardín", "brc": "2205", "cm": 22,
    "lat": 3.36, "lng": -76.53, "gp": "e",    // e = exacta, b = barrio
    "s": 78.4,                                 // puntaje 0-100; null = no ranqueable
    "c": [82, 70, 50, 64, 50],                 // C1..C5, de 0 a 100
    "d": 0.18,                                 // descuento vs comparables (0.18 = 18 % debajo)
    "up": 0.25,                                // upside al P75 en buen estado, sin obra nueva si es usado; tope 0.8 (UI: "≥ +80%")
    "conf": 0.9,
    "tags": ["nueva", "rebaja", "remodelar", "legal", "sospechoso", "mixto"],  // mixto: uso mixto/multifamiliar (T13)
    "pen": [{"k": "inund", "pts": 12, "why": "Zona inundable: río Cauca"}],
    "img": "https://…jpg",
    "fs": "2026-11-14",                        // primera vez visto
    "pd": -0.05,                               // cambio desde el primer precio (negativo = rebaja); null si no cambió
    "src": ["fr", "m2"],
    "dom": 12                                  // días en mercado
  }]
}
```

### `detail/<xx>.json`
`xx` son los 2 primeros caracteres hex de `md5(id)`. Es un objeto `id → detalle`:
```json
{ "fr-123": {
  "title": "…", "desc": "…", "photos": ["…"], "urls": ["https://fincaraiz…", "https://metrocuadrado…"],
  "admin": 350000, "floor": 5, "area_lot": null,
  "history": [{"d": "2026-11-14", "p": 330000000}, {"d": "2026-11-20", "p": 320000000}],
  "expected_m2": 5000000, "arv_m2": 5600000, "max_offer_70": 305760000,
  "comps": [{"id": "m2-9", "lat": 3.36, "lng": -76.53, "pm": 4900000, "a": 80, "dist": 320, "url": "…"}],
  "comps_info": "12 comparables a ≤ 800 m, mediana $5,0M/m²",
  "amen": [{"cat": "mall", "name": "Jardín Plaza", "dist": 650}, …],   // las 3 más cercanas por categoría
  "risks": [{"k": "homic", "why": "…"}],
  "projects": [{"k": "renov", "name": "Renovación R2", "dist": 300}],
  "zone": {"median_m2": 5100000, "trend_12m": 0.07, "dom": 45, "yield": 0.062}
}}
```

### `zones.json`
`{ "<barrio_code>": {"name", "comuna", "n_sale", "median_m2", "trend_12m", "dom", "yield", "n_rent"} }`

### `barrios.geojson`
Polígonos IDESC simplificados, con `properties.code` y `name`.

### `meta.json`
`{ "generated_at", "weights", "runs": [últimas 20 corridas], "layers": {"<capa>": "YYYY-MM-DD"}, "counts": {...} }`

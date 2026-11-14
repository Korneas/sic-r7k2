# Fuentes

Exploración del 2026-10-20. Ambas fuentes responden con HTTP simple (`httpx`), **sin Playwright**.

## Finca Raíz (`fincaraiz`)

**Viable. Datos en JSON embebido (`__NEXT_DATA__`).**

### URLs de búsqueda

Una URL por operación y tipo (robots.txt prohíbe las combinadas `*-y-*`, por ejemplo `casas-y-apartamentos`):

```
https://www.fincaraiz.com.co/{arriendo|venta}/{apartamentos|casas}/zona-norte/cali/3-o-mas-habitaciones?stratum[]=4&stratum[]=5&stratum[]=6
```

- `zona-norte/cali` es la zona del portal. Incluye el nororiente (Calima, Los Alcázares, etc.), que se descarta con el polígono y el estrato.
- `3-o-mas-habitaciones`: 3 o más. `3-habitaciones` es exacto.
- `stratum[]=N` se puede repetir.
- `garage[]=1` se convierte en `/1-parqueadero` y parece ser "exactamente 1". **No usarlo**: filtrar parqueadero en local.
- `desde-N` como filtro de precio interpreta dólares. No usarlo.

### Paginación

`/pagina2`, `/pagina3`, … insertado antes del query string. Son 21 por página. `props.pageProps.fetchResult.searchFast.paginatorInfo` trae `total`, `lastPage` y `hasMorePages`.

Volumen aproximado (estrato 4 a 6, 3 o más habitaciones):

| Búsqueda | Ofertas aprox. |
|---|---|
| arriendo/apartamentos | ~90 |
| arriendo/casas | ~50 |
| venta/apartamentos | ~560 (27 págs) |
| venta/casas | ~300 |

### Campos (`searchFast.data[]`)

| Campo nuestro | Campo FR |
|---|---|
| source_id | `id` |
| url | `"https://www.fincaraiz.com.co" + link` |
| title | `title` |
| price | `price.amount` |
| admin_fee | `commonExpenses.amount` |
| precio + admin | `price.admin_included` (usar para el filtro de arriendo) |
| bedrooms / bathrooms | `bedrooms` / `bathrooms` (`rooms` viene en 0, no usar) |
| parking | `garage` (0 puede significar "no reportado") |
| area_m2 | `m2Built` o `m2` |
| estrato | `stratum` |
| antigüedad | `construction_year` / `antiquity` (casi siempre `null`) |
| barrio | `locations.neighbourhood[-1].name` (trae varios; el último es el más específico) |
| lat / lng | `latitude` / `longitude` |
| descripción | `description` |
| características | `facilities[].name` (por ejemplo "Patio", "Zona de lavandería"…) |
| thumbnail | `img` |
| fecha | `created_at` |

## Metrocuadrado (`metrocuadrado`)

**Viable. API JSON interna que usa su propio front.**

La página es Next.js con App Router (sin `__NEXT_DATA__`, los datos van en el payload RSC). Los botones de paginación no cambian la URL, pero el front llama a:

```
GET https://www.metrocuadrado.com/api/rest-search/search
    ?size=50&from=0
    &realEstateTypeList=apartamento|casa
    &realEstateBusinessList=arriendo|venta
    &city=cali
    &roomList=3,4,5
    &stratumList=4,5,6
    &byPoint=true&latitude=3.475&longitude=-76.527&distance=4
```

- Desde nov-2026 se usa `/api/rest-search/search` (el proxy de su propio front, que añade la key en el servidor): mismos parámetros y respuesta, sin header. El endpoint directo `/rest-search/search` pide `X-Api-Key` y la key ya no aparece en el HTML.
- `byPoint` + `distance` (km) hace una búsqueda por radio. Sin eso, Cali completo devuelve ~4.000 apartamentos en venta. Con 4 km alrededor del norte baja a ~1.200 (sin filtro de estrato).
- **No usar `zone=norte`**: la zona "Norte" de M2 es muy estrecha y clasifica Granada, Juanambú y Normandía como "Oeste" u "Otros".
- Paginación con `from` (offset) y `size=50`. La respuesta trae `totalHits`. Los resultados incluyen destacados repetidos, así que se deduplica por `midinmueble`.
- Rarezas: con `from >= totalHits` puede devolver un `totalHits` distinto. Cortar cuando `from >= totalHits` de la primera página.

### Campos (`results[]`)

| Campo nuestro | Campo M2 |
|---|---|
| source_id | `midinmueble` |
| url | `"https://www.metrocuadrado.com" + link` |
| title | `title` |
| price | `mvalorarriendo` o `mvalorventa` |
| admin_fee | `data.mvaloradministracion` (string) |
| bedrooms / bathrooms / parking | `mnrocuartos` / `mnrobanos` / `mnrogarajes` (strings) |
| area_m2 | `mareac` o `marea` |
| estrato | `estrato` |
| barrio | `mbarrio` (en MAYÚSCULAS, a veces con prefijo "CR"…) |
| lat / lng | `localizacion.lat` / `localizacion.lon` |
| ubicación aproximada | `ubicacionaproximada` = "S" |
| descripción | `comment` |
| características | `featured[]` ("clave:valor"): `conZonaLavanderia:S`, `tiempoConstruido:…`, `conPisoPorcelanato_o`, `ascensor`, `conPiscina`… |
| thumbnail | `imageLink` |

## Coordenadas por defecto

Algunas ofertas traen coordenadas genéricas, por ejemplo `3.4516,-76.532` o `3.4507,-76.5405`, compartidas por inmuebles de barrios distintos (Pance y Ciudad Jardín aparecen ahí). Regla: si ≥3 ofertas de barrios distintos comparten exactamente la misma coordenada, se trata como no exacta y se geocodifica por barrio (`geo_precision: neighborhood`).

## robots.txt

- **Finca Raíz**: `User-agent: *` permite las búsquedas por tipo. Prohíbe `/arriendo/*-y-*`, `/venta/*-y-*`, `/mobile/`, `/apps/` y otras rutas de admin. Cumplimos usando una URL por tipo.
- **Metrocuadrado**: `User-agent: *` prohíbe `/detail/`, `/web/`, `/servlet/`, `/metrocuadrado-results/`, `/_Incapsula_Resource?` y otras. Las búsquedas (`/apartamento/arriendo/...`) , `/rest-search/` y `/api/` no están prohibidas. No entramos a páginas de detalle.

## Anti-bot

- **Finca Raíz**: sin desafío desde IP residencial.
- **Metrocuadrado**: el robots.txt menciona Incapsula (Imperva), pero no hubo desafío desde IP residencial.
- **Pendiente**: probar desde una IP de GitHub Actions (datacenter), que es el riesgo principal.

## Ritmo

Entre 3 y 7 s aleatorios entre requests y todo secuencial. Una corrida completa son ~50 requests a FR y ~40 a M2, unos 8 minutos.

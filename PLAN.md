# sic-r7k2 (scraper inmuebles Cali): plan de requisitos e implementación

> Versión 1, 2026-11-14. Documento vivo: se actualiza al cerrar cada fase (sección 13, bitácora).

## 1. Objetivo

Encontrar en Cali **casas y apartamentos usados en venta** cuyo precio esté por debajo de lo que vale la zona, en sectores con buena valorización, buenas referencias y reventa ágil, para **comprarlos, remodelarlos con un contratista y revenderlos más caros** (fix & flip).

El sistema responde tres preguntas por cada oferta:

1. **¿Está barata?** Precio/m² frente a ofertas comparables cercanas.
2. **¿Cuánto podría subir?** Distancia entre su precio/m² y el de los mejores comparables de la zona (ARV).
3. **¿La zona acompaña?** Valorización, amenidades, liquidez y riesgos, cada uno con su razón explícita.

### Decisiones tomadas (respuestas del 2026-11-14)

| Tema | Decisión |
|---|---|
| Estrategia | Fix & flip: comprar barato, remodelar, revender |
| Operaciones | **Venta** se ranquea y se muestra. **Arriendo** se scrapea solo para indicadores de zona (rentabilidad, demanda) y no se muestra como oferta |
| Tipos | Casas y apartamentos. Fuera: proyectos sobre planos, lotes, locales y oficinas |
| Presupuesto | Sin tope. Los filtros viven en la UI |
| Estrato | **Mínimo 2** (filtro duro). Se ajustó de 3 a 2 a mitad de la planeación |
| Ciudad | Solo Cali urbana |
| Riesgo | Penaliza el puntaje y **muestra la razón** de cada penalización |
| Pesos | Fijos, según la investigación de la sección 6. Se recalibran con datos (fase 8) |
| Mapa | **Mapbox GL JS** con el token público (`pk.*`) entregado por el usuario |
| Costos | Cero pagos. La sección 10 lista qué se ganaría pagando |
| Deduplicación | Fusionar entre portales |
| Historial | Guardar cada cambio de precio y marcar rebajas |
| Almacenamiento | SQLite en el repo. Se migra si crece demasiado (sección 5.4) |
| Alertas | No |
| Estado personal | Solo **favoritos** (❤) y un filtro "solo favoritos". Sin sincronización entre dispositivos |
| Repo | Nuevo, **público** (Actions gratis) con nombre poco buscable: `sic-r7k2` |
| Autonomía | Total: crear el repo, hacer push, configurar Actions y desplegar en Vercel |
| UI | Referencias de Mobbin (sección 8.1) |

## 2. Lo que se reutiliza de apto-tracker

Copiar y adaptar, sin importarlo como dependencia:

- `scraper/sources/fincaraiz.py` y `metrocuadrado.py`: parsers y mapeo de campos (ver `apto-tracker/docs/sources.md`).
- `scraper/geo.py`: detección de coordenadas genéricas (≥3 ofertas de barrios distintos en la misma coordenada se tratan como no exactas).
- `scraper/dedupe.py`: deduplicación dentro de un portal.
- La lógica de retiro: una oferta que falta en N corridas pasa a `status = removed`, y para las favoritas se verifica la página del anuncio.
- `vercel.json`: estático con `noindex`, sin indexar.

Lo que **no** se reutiliza: los filtros del norte, el cupo diario, el Gist ni `explorar.html`.

## 3. Requisitos funcionales

### RF1. Scraping
- RF1.1 Scrapear Finca Raíz (FR) y Metrocuadrado (M2): Cali, venta y arriendo, apartamento y casa, estratos 2 a 6.
- RF1.2 Corridas **cada 6 horas** (00:00, 06:00, 12:00 y 18:00 COT).
- RF1.3 **Sin repetir publicaciones**: un ID estable por oferta (`fr-<id>`, `m2-<id>`), upsert en SQLite y fusión entre portales (RF3).
- RF1.4 Detectar ofertas nuevas, cambios de precio, retiradas (`removed`) y republicadas (vuelven después de retirarse).
- RF1.5 Respetar `robots.txt` (ver `docs/sources.md` de apto-tracker), ir en secuencia, con 3 a 7 s de pausa aleatoria y sin entrar a páginas `/detail/` de M2.
- RF1.6 Excluir proyectos sobre planos y nuevos (M2: `link` empieza por `/proyecto/`; FR: flag de proyecto o tipo), además de oficinas y locales por palabras clave.
- RF1.7 Registrar cada corrida (`runs`): duración, requests, nuevas, actualizadas, retiradas y errores por fuente.

### RF2. Volumen y estrategia de corrida
Medido el 2026-11-14 (Cali, estratos 2 a 6):

| Búsqueda | FR | M2 |
|---|---|---|
| venta apartamentos | 6.168 (294 págs × 21) | 5.938 |
| venta casas | 3.314 (158 págs) | 2.785 |
| arriendo apartamentos | 1.030 | 992 |
| arriendo casas | 341 | 266 |
| **Total** | **~10.850 (~520 págs)** | **~9.980 (~200 págs × 50)** |

A 5 s promedio por request, un barrido completo son unos 720 requests, es decir **~60 min**. Por eso hay dos modos:

- **Barrido completo** una vez al día (00:00 COT): recorre todo, actualiza precios y detecta retiradas.
- **Incremental** en las otras 3 corridas: ordena por más recientes y corta después de 2 páginas seguidas sin IDs nuevos (~5 a 10 min).
  - *A verificar en la fase 1*: el parámetro de orden "más recientes" en FR (`?ordenListado=...` u otro) y en M2 (`sort=...`). Si no existe, el incremental recorre las primeras K páginas por estrato.
- Si un portal limita la profundidad de paginación (por ejemplo, máximo 100 páginas), se parte la búsqueda por estrato o por rango de precio hasta quedar debajo del límite.

### RF3. Deduplicación entre portales
Varias inmobiliarias publican el mismo inmueble en los dos portales. Dos ofertas son la misma propiedad si cumplen todo esto:

- mismo tipo, mismas habitaciones y mismo estrato (si ambos lo reportan);
- área a ±5% o ±3 m²;
- precio a ±5%;
- distancia < 250 m si ambas coordenadas son exactas; si alguna es aproximada, mismo barrio.

Los grupos se arman con union-find. El canónico es la oferta vista primero, y la tarjeta muestra las URLs de todas. Fase 7: hash perceptual de la primera foto para confirmar o romper grupos dudosos.

### RF4. Enriquecimiento geográfico
Por cada oferta: barrio y comuna oficiales (polígonos IDESC), estrato oficial de la manzana, amenidades cercanas, riesgos y proyectos urbanos cercanos. Ver la sección 5.

### RF5. Puntaje
Puntaje de 0 a 100 con componentes explicables y penalizaciones con su razón. Ver la sección 6.

### RF6. Interfaz web
- Lista y mapa sincronizados, con orden por puntaje por defecto.
- Filtros: precio, precio/m², área, habitaciones, baños, parqueadero, estrato, tipo, comuna o barrio, puntaje mínimo, descuento mínimo, upside mínimo, 🔨 para remodelar, "con rebaja", "nueva (< 48 h)", "sin penalizaciones", "solo favoritos" y antigüedad del anuncio.
- Detalle con fotos, desglose del puntaje, penalizaciones con razón, cálculo del flip, historial de precios, comparables (en el mapa y en lista), enlaces a los portales y amenidades cercanas.
- Favoritos (❤) en `localStorage`, con su filtro.
- Capas opcionales del mapa: heatmap del precio/m² mediano, amenaza de inundación, tratamientos de renovación y troncales proyectadas.
- Página "Metodología" que explica la fórmula, sus fuentes y las corridas recientes.

## 4. Requisitos no funcionales

- **Costo $0**. GitHub Actions necesita un **repo público**: un repo privado tiene 2.000 min/mes y el plan consume ~2.700 (60 min/día del barrido + 3 × 10 min de incrementales). Los datos ya son públicos en los portales, y el sitio se publica con `noindex`.
- Sitio estático en Vercel. Sin servidor ni base de datos en línea.
- Tiempo de carga < 3 s en 4G con unas 9.000 ofertas de venta (JSON comprimido < 1,5 MB y detalle cargado bajo demanda).
- Mobile-first: en el teléfono, lista y mapa se alternan; en desktop van lado a lado.
- Accesibilidad básica: contraste, foco visible y el puntaje expresado también en texto, no solo en color.
- Token de Mapbox: es público (`pk.*`), pero se **restringe por URL** en el dashboard de Mapbox (dominio de Vercel y `localhost`).
- Cada lógica no trivial (dedupe, comparables, puntaje, flip) deja **un** check ejecutable (`python -m scraper.check_<x>` imprime "ok").

## 5. Datos

### 5.1 Fuentes de ofertas
FR (`__NEXT_DATA__`, URL `/venta/{apartamentos|casas}/cali/valle-del-cauca?stratum[]=2..6`) y M2 (`/api/rest-search/search`, `city=cali`, `size=50`; con `size` pequeño devuelve 0). Campos según `apto-tracker/docs/sources.md`.

Campos adicionales a capturar para el flip:

- FR: `construction_year` y `antiquity`, piso, `facilities`, `description` completa y todas las fotos.
- M2: `featured` (`tiempoConstruido`, ascensor, etc.), `comment` y piso.

### 5.2 Fuentes geográficas (gratis)
IDESC Cali publica un **WFS abierto** en `https://ws-idesc.cali.gov.co/geoserver/wfs` (verificado el 2026-11-14, con 347 capas y salida GeoJSON en `srsName=EPSG:4326`). Las capas útiles:

| Capa | Uso |
|---|---|
| `dapm:pdt_dpa_barrios_sectores` (342 polígonos, Acuerdo 0636 de 2026) | Barrio y comuna oficiales |
| `dapm:pdt_dpa_comunas` | Comuna |
| `dapm:pdt_est_estrato_urbano_expansion` (720k predios, sin geometría, con código de manzana) | Estrato oficial por manzana, unido a `catastro:cat_bas_manzanas` |
| `catastro:cat_bas_terrenos` (`zona_econ1`, uso, área construida, pisos) | Zona homogénea económica; verificar si hay tabla de valor de terreno por ZHE |
| `seguridad_justicia:ps_os_homicidios` (17.993 puntos, 2012 a 2025) | Densidad de homicidios de los últimos 24 meses en un radio de 500 m |
| `pot_2014:amb_ari_inundacion_fluvial`, `amb_ari_inundacion_pluvial`, `expediente_municipal:emc_amb_ari_amenaza_no_mitigable_inundacion` | Penalización por inundación |
| `pot_2014:amb_ari_movimientos_masa` | Penalización por remoción en masa (ladera) |
| `pot_2014:nur_tratamientos_urbanisticos` (renovación R1/R2/R3, consolidación, etc.) | Bonificación por renovación urbana |
| `pot_2014:nur_areas_generadoras_plusvalia`, `emru:emru_pcp_planes_parciales`, `emru:emru_pcp_ciudad_paraiso` | Bonificación por proyectos que valorizan |
| `pot_2014:mov_sitm_troncales_proyectadas` (Corredor Verde Cl 25, etc.), `pot_2014:mov_corredor_ferreo` (tren de cercanías) | Infraestructura futura |
| `pot_2014:eqp_uco_salud`, `salud:ads_ips_capacidad_reps`, `pot_2014:eqp_uco_educacion`, `pot_2014:eqp_uco_recreacion`, `deporte_recreacion:escenarios_deportivos` | Amenidades oficiales |
| `dagma:obs_rui_*` (ruido) | Penalización leve por ruido nocturno |
| `vivienda:pmih_barrios_precarios` | Penalización por entorno |

**OpenStreetMap** (Overpass API, una descarga semanal de Cali completa y cacheada): centros comerciales, supermercados, restaurantes y cafés, parques, estaciones del MIO, universidades, clínicas y hospitales, bancos.

**Datos de mercado (contexto, no por oferta):** el IPVU Cali del Banco de la República (anual, por ciudad) sirve para comparar la valorización de cada barrio con la de la ciudad. El portal `datos.cali.gov.co` estaba en "suspensión temporal de servicios web" el 2026-11-14; IDESC lo reemplaza.

Las capas estáticas se descargan con `scripts/fetch_layers.py` a `data/layers/*.geojson`, simplificadas y versionadas. Se refrescan cada mes, no en cada corrida.

### 5.3 Modelo de datos (SQLite `data/sic.db`)
```sql
listing(id PK, source, source_id, url, operation, property_type, title, description,
        price, admin_fee, area_m2, price_m2, bedrooms, bathrooms, parking, floor,
        age_years, stratum_portal, lat, lng, geo_precision, neighborhood_portal,
        photos JSON, features JSON, published_at, first_seen, last_seen,
        status,              -- active | removed
        removed_at, missed_runs, group_id)
price_change(listing_id, seen_at, price)          -- solo cuando cambia
listing_geo(listing_id PK, barrio_code, barrio, comuna, stratum_official,
            amenities JSON, risks JSON, projects JSON)
listing_score(listing_id PK, computed_at, score, components JSON, penalties JSON,
              expected_m2, arv_m2, flip JSON, comps JSON, confidence)
zone_stats(barrio_code, date, type, n_sale, median_m2_sale, n_rent, median_m2_rent,
           gross_yield, median_dom, absorption)   -- snapshot diario
run(id, started_at, mode, source, requests, new, updated, removed, errors, seconds)
```

La web no lee SQLite. El build exporta:

- `web/data/listings.json`: ventas activas con los campos de lista y mapa más el puntaje (~1 MB gzip).
- `web/data/detail/<shard>.json`: descripción, fotos, historial, comparables y desglose, partido en ~100 archivos por prefijo de ID.
- `web/data/zones.json` y `web/data/meta.json`.

### 5.4 Límites de SQLite en git
Estimado: ~25k filas activas más histórico, unos 30 a 60 MB. Cada commit de un binario cambiado suma su tamaño al historial de git.

Mitigaciones, en orden:
1. Commitear la DB **solo en el barrido diario**. Los incrementales guardan la DB como artefacto o cache de Actions y la restauran.
2. `VACUUM` y no guardar HTML crudo.
3. Si `.git` pasa de ~1 GB, mover la DB a un **asset de GitHub Release** (gratis, sobrescribible) y dejar en git solo los exports JSON. El cambio de código es mínimo: descargar al inicio y subir al final.

## 6. Puntaje: metodología

### 6.1 Principios (de la investigación)
- **Comparables como base.** El método de comparación de mercado (CMA, AVM con k vecinos) es el estándar para estimar valor, y su precisión depende sobre todo de cuántos comparables cercanos haya ([Microburbs, AVM accuracy](https://www.microburbs.com.au/research/avm-accuracy-whitepaper); [arXiv 2603.12986](https://arxiv.org/html/2603.12986v1)). Un kNN puro rinde menos que modelos hedónicos o con boosting ([Gobierno de Gales, 2026](https://www.gov.wales/sites/default/files/publications/2026-03/testing-land-valuation-methods-tesseract-academy-report.pdf)), así que la v2 agrega un modelo hedónico.
- **Precios de oferta, no de cierre.** Comparamos ofertas contra ofertas, y los dos lados tienen el mismo sesgo. Hay outliers por precios "a capricho del vendedor" ([estudio de Ereván](https://jaer.nuaca.am/index.php/jaer/article/download/92/41/140)), así que se usan **medianas y MAD**, no promedios.
- **Regla del 70% para el flip.** Compra máxima = ARV × 0,70 − remodelación (aquí sin remodelación, solo como referencia), y la referencia baja a 60–65% en mercados lentos o riesgosos ([BiggerPockets](https://biggerpockets.com/blog/70-percent-rule), [Kiavi](https://www.kiavi.com/blog/what-is-70-rule-in-house-flipping)).
- **Amenidades por distancia con decaimiento**, como Walk Score: puntaje completo a ≤400 m (5 min a pie) y cero a ~2,4 km ([metodología Walk Score](https://www.walkscore.com/methodology.html), [replicación en Zenodo](https://zenodo.org/records/10899286/files/submission_21.pdf)). Los pesos por categoría no son públicos, y las replicaciones usan pesos iguales o arbitrarios.
- **El efecto de cada amenidad no es uniforme.** Los parques suelen sumar, pero depende del estado y del entorno ([Brander & Koetse 2011](https://personal.lse.ac.uk/WUW2/pdf/2012_Spatial%20Variations%20in%20Amenity%20Value.pdf)). Los hospitales grandes a distancia caminable pueden restar por tráfico y ruido ([estudio de Wuhan, HKU](https://repository.hku.hk/bitstream/10722/259459/1/Content.pdf)). Por eso los hospitales suman a 400–1.500 m y no adyacentes.
- **Transporte masivo valoriza.** TransMilenio: +6,8% a 9,3% por cada 5 min menos a pie hasta la estación. BRT de Brisbane: +0,14% por cada 100 m. En Cali, el MIO y la cercanía a vías principales explican precios ([EAFIT](https://repository.eafit.edu.co/bitstreams/d05c65bc-e28d-4571-baa1-fdc16eaa4389/download), [Univalle](https://bibliotecadigital.univalle.edu.co/bitstreams/51900e7a-8892-4f9b-a1cf-95f6739379ef/download)). El metro de Bogotá muestra **efecto anticipación** antes de que exista la obra ([JTLU](https://jtlu.org/index.php/jtlu/article/view/2593)), lo que justifica bonificar las troncales proyectadas y el tren de cercanías.
- **Contexto de Cali.** Vivienda usada +7,7% ([Occidente, oct-2026](https://occidente.co/empresario/vivienda-usada-se-valoriza-en-77/)), escasez de oferta entre $300M y $700M, y "el m² más barato del país" según la Lonja ([Occidente, jun-2026](https://occidente.co/?p=95499)). Tren de cercanías Cali–Jamundí con 21 estaciones, 11 conectadas al MIO, en factibilidad ([Occidente](https://occidente.co/cali/tren-de-cercanias-del-valle-desarrollo-regional-cali/)): bonificación con peso bajo por la incertidumbre.
- **Combinación por MCDA de suma ponderada**: cada componente se normaliza a un **percentil de 0 a 100 dentro de Cali** antes de ponderar, para que ninguna escala domine. Es el enfoque estándar de selección multicriterio. Los pesos se recalibran en la fase 8 contra resultados observados.

### 6.2 Componentes

**C1. Descuento frente a comparables (35%)**
- Comparables: misma operación (venta) y tipo, activas o vistas en los últimos 90 días, área a ±30%, estrato a ±1, en un radio de 800 m (se amplía a 1,5 km y luego al barrio hasta tener k ≥ 8). Se usan como máximo los 20 más cercanos, ponderados por distancia y similitud de área.
- `expected_m2` = mediana ponderada del precio/m² de los comparables. v2: predicción hedónica (sección 6.4).
- `discount = 1 − price_m2 / expected_m2`. Se ignora si `|z_MAD| > 4` y el precio es irreal, por ejemplo un arriendo publicado como venta o un error de dígitos: queda marcado como "dato sospechoso" y no se ranquea.
- Una rebaja reciente (< 30 días) se muestra como etiqueta y no suma doble.

**C2. Potencial de reventa (25%)**

*Sin costos de remodelación (decisión del 2026-11-14): se mide el techo de precio de la zona, no la ganancia neta.*
- `ARV_m2` = percentil 75 del precio/m² de los comparables "en buen estado": antigüedad ≤ 15 años o palabras como "remodelado" o "para estrenar". Si hay menos de 5, se usa P75 de todos.
- `upside = ARV_m2 / price_m2 − 1`: cuánto podría subir el m² si queda como los mejores de la zona. Se muestra en pesos (`(ARV_m2 − price_m2) × area`) y en %.
- Si el texto o la antigüedad indican "para remodelar", "para actualizar", "original" o más de 20 años, aparece la etiqueta 🔨 "para remodelar". Es informativa: no resta y ayuda a encontrar los candidatos del flip.
- También se muestra `max_offer_70 = ARV × 0,70` como referencia de la regla del 70%, comparada con el precio pedido.
- El componente es el percentil del `upside`. Si el `upside` es menor o igual a 0, C2 vale 0.

**C3. Valorización de la zona (15%)**
- **Tendencia** (60% de C3): pendiente del log de la mediana del precio/m² del barrio (o de la comuna si n < 15) en los últimos 6 a 12 meses, a partir de `zone_stats`. **Arranque en frío:** sin historia propia, este subcomponente vale 50, que es neutro, y se muestra "sin datos aún". Cada barrio se compara con el IPVU Cali.
- **Proyectos** (40% de C3), sumando con tope de 100:
  - renovación urbana R1/R2/R3 o plan parcial a ≤ 500 m: +40;
  - área generadora de plusvalía: +25;
  - troncal MIO proyectada o Corredor Verde a ≤ 800 m: +30;
  - corredor férreo (tren) a ≤ 1 km: +15, con peso bajo por la incertidumbre.

**C4. Amenidades (15%)**

Estilo Walk Score, con decaimiento `f(d) = 1` si d ≤ 400 m, luego lineal hasta 0 en 2.400 m. Las distancias son en línea recta × 1,3 para aproximar la caminata; la v2 usa la red vial de OSM.

| Categoría | Peso | Profundidad | Fuente principal | Respaldo | Conteo en Cali (verificado el 2026-11-14) |
|---|---|---|---|---|---|
| Estación MIO troncal o terminal | 15 | más cercana | GTFS del MIO (MobilityData mdb-3356; las estaciones troncales casi no cambian) | OSM `public_transport=station` | ~61 estaciones y terminales |
| Parada MIO pretroncal o alimentadora | 5 | más cercana | GTFS del MIO | OSM `highway=bus_stop` | ~1.878 paradas (Metrocali, 2024) |
| Centro comercial | 15 | más cercano | OSM `shop=mall` y una lista curada de los grandes (Chipichape, Unicentro, Jardín Plaza, Palmetto, Cosmocentro, Pacific Mall, Centenario, Premier Limonar, Único, etc.) con peso doble | | 57 en OSM |
| Supermercado | 12 | 2 más cercanos | OSM `shop=supermarket` (Éxito, Carulla, Jumbo, Olímpica, D1, Ara…) | | 183 |
| Parque o zona verde | 13 | 2 más cercanos, ponderados por área | OSM `leisure=park` | IDESC `pot_2014:eqp_uco_recreacion` y `dagma` zonas verdes | 470 |
| Clínica u hospital | 10 | más cercano, a 400–1.500 m; < 150 m suma 0 por tráfico y ruido | IDESC `salud:ads_ips_capacidad_reps` (oficial, con capacidad: se priorizan IPS con camas o urgencias) | OSM `amenity=hospital,clinic` | 1.062 registros en IDESC, 437 en OSM |
| Colegios y universidades | 10 | 2 más cercanos | IDESC `educacion:esf_establecimiento_educativo` | OSM `amenity=school,university,college` | 276 en OSM |
| Restaurantes y cafés | 8 | 10 más cercanos (densidad) | OSM `amenity=restaurant,cafe` | | 459 (cobertura parcial: se usa como densidad relativa, no absoluta) |
| Bancos y droguerías | 4 | 2 más cercanos | OSM `amenity=bank,pharmacy` | | 304 |
| Gimnasios y escenarios deportivos | 3 | más cercano | OSM `leisure=fitness_centre,sports_centre` | IDESC `deporte_recreacion:escenarios_deportivos` | 133 |
| Ciclorrutas | 0 (solo se muestra) | | IDESC `pot_2014:mov_jerarquizacion_ciclorutas` | | |

Notas:
- **Fuentes gratis**: OSM vía Overpass (una descarga semanal con bbox de Cali, ~10 s), IDESC WFS (mensual) y el GTFS del MIO (mensual).
- **Calidad de OSM en Cali**: buena en centros comerciales, supermercados, parques y salud; parcial en restaurantes. Por eso los restaurantes pesan poco y se usan como densidad relativa. Con Google Places (sección 10) se sumarían ratings y cobertura completa.
- **Centros comerciales grandes**: una lista curada en `config/anchors.yaml` (~15 malls principales con coordenada) pesa doble, porque un centro comercial grande pesa más en el valor que uno de barrio.
- El detalle muestra las 3 amenidades más cercanas por categoría con nombre y distancia ("Unicentro a 650 m · MIO Universidades a 300 m · Parque del Perro a 900 m"). En el mapa del detalle se pueden activar como capa.
- La v2 calibra los pesos con un modelo hedónico: la contribución real de cada distancia al precio/m² en Cali.

**C5. Liquidez de reventa (10%)**

Para un flip, vender rápido vale tanto como comprar barato.
- Días en mercado (DOM) medianos de las ventas retiradas del barrio, usados como proxy de venta (menos días es mejor).
- Absorción: retiradas en 30 días ÷ activas.
- Arranque en frío: vale 50 hasta tener 30 días de historia.

**Estrato**: es un filtro duro (≥ 2) y no suma al puntaje, porque ya está reflejado en los comparables. Se muestra como etiqueta. Se usa el estrato oficial de IDESC; si falta, el del portal.

### 6.3 Penalizaciones (restan puntos y siempre muestran su razón)

| Condición | Resta | Razón mostrada |
|---|---|---|
| Amenaza **no mitigable** por inundación (POT) | −30 | "Zona de amenaza no mitigable por inundación (POT 2014)" |
| Amenaza por inundación fluvial o pluvial | −12 | "Zona inundable: río Cauca o pluvial" |
| Amenaza por movimientos en masa | −15 | "Ladera con amenaza de remoción en masa" |
| Homicidios en 500 m (24 meses) ≥ P90 de Cali | −15 (≥ P75: −7) | "N homicidios a 500 m en 24 meses (P90 de la ciudad)" |
| Barrio en el programa de mejoramiento o precario | −10 | "Entorno de mejoramiento integral" |
| Ruido nocturno alto (DAGMA) | −5 | "Ruido nocturno > X dB" |
| Palabras de riesgo legal ("derechos", "proindiviso", "sucesión", "sin escritura", "posesión", "remate", "embargo") | −10 y etiqueta ⚖️ | "Posible tema legal: «…»". Puede ser oportunidad, así que **no se oculta** |
| Coordenada aproximada | sin resta; baja la confianza | "Ubicación aproximada: el análisis de zona usa el barrio" |

### 6.4 Fórmula final
```
base   = 0.35·P(C1) + 0.25·P(C2) + 0.15·C3 + 0.15·C4 + 0.10·C5      # P = percentil 0-100
score  = clamp(base − Σ penalizaciones, 0, 100) · (0.6 + 0.4·confianza)
confianza = min(1, k_comparables/12) · (1 si geo exacta, 0.7 si barrio) · (1 si hay área, 0)
```
Filtros de la UI relacionados: "upside mínimo" y "solo 🔨 para remodelar". Requisitos para ranquear: `price`, `area_m2` > 20, estrato ≥ 2 y `discount` > 0. Lo demás aparece con el puntaje en gris y la etiqueta "no es oportunidad".

**v2 (fase 7): modelo hedónico.** Regresión robusta (Huber o LightGBM con objetivo L1) de `log(price_m2)` sobre área, habitaciones, baños, parqueadero, antigüedad, piso, estrato, tipo, efecto fijo de barrio y distancias. Se valida con k-fold espacial por comuna. Si el MAPE fuera de muestra mejora al kNN en más de 10%, reemplaza a `expected_m2`. La residual sigue siendo el descuento.

### 6.5 Validación
- **Backtest proxy**: tomar como buenas compras las ventas retiradas en menos de 21 días y verificar que el puntaje sí las separa (AUC > 0,6). Con eso se recalibran los pesos.
- Revisión manual del top 20 cada semana: el usuario marca ❤ o ignora y se registra.
- Cada número del detalle muestra cómo se calculó ("8 comparables a ≤ 800 m, mediana $4,2M/m²").

## 7. Arquitectura

```
GitHub Actions (cron 6 h, repo público)
  scraper/main.py --mode full|incremental
    sources/{fincaraiz,metrocuadrado}.py  →  upsert SQLite
    dedupe.py (intra y entre portales)
    geo.py (barrio/comuna/estrato vía data/layers, punto en polígono con shapely + STRtree)
    zones.py (zone_stats diario)
    score.py (comparables, flip, componentes, penalizaciones)
    export.py → web/data/*.json
  commit (DB solo en full) y push → Vercel redespliega web/
Web estática: web/index.html + app.js + Mapbox GL JS (sin framework y sin build)
```

- Python 3.12. Dependencias: `httpx`, `pyyaml`, `shapely` (punto en polígono y buffers sobre unos 9.000 puntos), `numpy`. `scikit-learn` o `lightgbm` solo en la v2.
- Frontend sin framework, como apto-tracker: HTML, CSS y JS vanilla con `mapbox-gl@3.x` fijado por versión desde CDN. Se reconsidera solo si la UI se complica.
- Mapa: fuente GeoJSON con `cluster: true`. Pins con el precio abreviado ("$320M") y color por puntaje en una escala secuencial validada para daltonismo. Las capas de riesgo y proyectos se cargan bajo demanda.

## 8. UI/UX

### 8.1 Referencias de Mobbin
- **Mapa y lista lado a lado con filtros en chips**: [Zillow search](https://mobbin.com/screens/1899b9a4-752f-483c-9798-3b16ea1b074f), [Airbnb con pins de precio](https://mobbin.com/screens/4b9d614f-7f68-47bb-83e6-7fd35efca097), [Fresha con mapa ocultable](https://mobbin.com/screens/3b46a801-c4a6-4ff1-93ea-6e1a2ad8b648).
- **"Buscar al mover el mapa"**: [Tripadvisor](https://mobbin.com/screens/5d6f6163-5f61-41de-9009-2f634676b1c0), [Navan](https://mobbin.com/screens/0b7cc27d-c1e8-43e4-817b-96da326d0629).
- **Lista ranqueada numerada**: Tripadvisor (como arriba).
- **Lista flotante sobre el mapa** (vista móvil): [Sweatpals](https://mobbin.com/screens/9b710af5-456b-4ee9-9b83-605ebace769f).
- **Historial de precios en tabla con % de cambio**: [Zillow market value](https://mobbin.com/screens/c105ccb0-8726-49c8-851c-6bce01e8b6f4).
- **"Qué hay cerca" con distancias a pie**: [KAYAK](https://mobbin.com/screens/3d8d2637-d499-4c53-9d89-b240d7e4aa05), [Klook](https://mobbin.com/screens/f2adef1e-049e-433e-bbeb-d7d138580bf9).
- **Detalle en panel lateral sin perder la lista**: [Care.com](https://mobbin.com/screens/cf804e97-c3f5-4916-9e0d-ae05e68c8e4b).
- **Galería de fotos en grilla**: [Expedia](https://mobbin.com/screens/89862e1a-a326-4366-88f8-b09fa2f45294).

En la fase 4 se hace una segunda ronda en Mobbin para filtros con rangos, comparadores de dos ofertas y estados vacíos.

### 8.2 Pantallas
1. **Explorar** (`/`). En desktop: barra de filtros en chips arriba; lista a la izquierda (~420 px) con tarjetas ordenadas por puntaje; mapa a la derecha.
   - La tarjeta muestra:
     - foto, precio y precio/m²;
     - un badge con el puntaje y su color;
     - "−18% vs zona" y "upside +25%";
     - barrio, estrato, m², habitaciones, baños y parqueadero;
     - íconos de penalización con tooltip, ❤, y las etiquetas "nueva" y "rebaja".
   - Al pasar sobre una tarjeta se resalta su pin, y al pasar sobre un pin se resalta su tarjeta. El toggle "Buscar en esta área" viene activado por defecto.
   - En móvil: el mapa ocupa la pantalla, la lista va en una hoja inferior arrastrable y hay un botón "Lista / Mapa".
2. **Detalle** (panel lateral en desktop, pantalla completa en móvil):
   - galería;
   - resumen ("Por qué está aquí"): las 3 razones principales y las penalizaciones con razón;
   - **desglose del puntaje** en barras C1 a C5;
   - **potencial de reventa**: ARV, upside en $ y %, y la referencia del 70%;
   - comparables, en lista y en un minimapa;
   - historial de precios;
   - qué hay cerca;
   - riesgos y proyectos;
   - descripción y enlaces a FR y M2 (todas las URLs del grupo).
3. **Zonas** (`/zonas`): coropleta por barrio de la mediana de precio/m², la tendencia, el DOM y la rentabilidad por arriendo, con un ranking de barrios.
4. **Metodología** (`/metodologia`): la sección 6 en lenguaje llano, las fuentes, la fecha de cada capa y las últimas corridas.

### 8.3 Reglas visuales
Cifras con `font-variant-numeric: tabular-nums`. Plata en COP abreviado ($320M, $4,2M/m²). Dark mode con tokens CSS. El puntaje siempre va acompañado de texto y nunca depende solo del color. Estados vacíos con una acción ("Quita el filtro X").

## 9. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Anti-bot en IPs de GitHub Actions (M2 usa Incapsula) | Fase 1: correr `probe.yml` desde Actions primero. Si bloquea, bajar el ritmo, rotar el user-agent o hacer fallback a correr en local con `launchd` en el Mac y push |
| Los portales cambian la API (ya pasó con la apiKey de M2) | Si una fuente devuelve 0 o menos del 50% de la corrida anterior, se marca `run.errors`, **no** se retiran ofertas en esa corrida y aparece un banner en el sitio |
| Pocos comparables en barrios con poca oferta | Radio expandible y confianza más baja, visible en la UI |
| Precio/m² engañoso (área de lote vs construida en casas) | Para casas, preferir `m2Built` o `mareac`. Si solo hay área de lote, baja la confianza |
| Arranque en frío de la tendencia y la liquidez | Valor neutro de 50 con la etiqueta "sin datos aún". Mejora sola con las corridas |
| Repo pesado | Sección 5.4 |
| IDESC caído | Las capas se versionan en el repo y se refrescan cada mes, sin depender de IDESC en cada corrida |

## 10. Opciones de pago (no se implementan; para decidir después)

| Opción | Costo aprox. | Beneficio |
|---|---|---|
| **Claude API (Haiku)** para leer descripción y fotos | ~US$2–10/mes con ~300 ofertas nuevas al día | Mayor salto en calidad: estimar el estado del inmueble desde las fotos (para remodelar, remodelado u original) y el nivel de remodelación. Mejora la etiqueta 🔨 y el ARV, hoy basados en palabras clave |
| Google Places API | Cuota gratuita por SKU y luego ~US$32 por 1.000 | Más densidad de POIs, ratings y reseñas como señal de calidad de la zona. OSM tiene buena cobertura en Cali, pero sin ratings |
| Proxy residencial (Bright Data, Apify) | US$10–50/mes | Solo si las IPs de Actions quedan bloqueadas |
| GitHub Actions privado | ~US$0,008/min sobre 2.000 min (~US$6/mes) | Repo privado |
| Mapbox por encima de 50.000 cargas/mes | US$5 por 1.000 | Irrelevante para uso personal |
| Certificado de tradición (SNR/VUR) | ~COP $20.000 c/u | Revisión legal del top que se va a visitar; manual y fuera del sistema |
| Datos transaccionales (Lonja, La Haus, Galería Inmobiliaria) | Por cotización | Precios de cierre en vez de oferta, lo que da un ARV mucho más preciso |

## 11. Plan de implementación (fases ejecutables en un día)

Cada fase termina con sus checks pasando, un commit y una entrada en la bitácora (sección 13).

**F0. Repo (≈15 min)**
- `gh repo create sic-r7k2 --public` (sin descripción ni topics, para que no aparezca en búsquedas), estructura de carpetas, `config.yaml`, `.gitignore`, `README.md` corto y este PLAN.md.
- ✅ El repo existe y se clona.

**F1. Scrapers y SQLite (≈2 h)**
- Portar las fuentes. Cali completa con estratos 2 a 6, venta y arriendo, excluyendo proyectos.
- Esquema SQLite, upsert, `price_change`, retiradas con protección contra caídas de una fuente, `run`.
- Investigar el orden "más recientes" y el límite de paginación de cada portal, y escribir `docs/sources.md`.
- Modos `full` e `incremental`.
- ✅ Una corrida `full` local llena unas 20k filas. Una segunda corrida inmediata da 0 nuevas. `check_upsert` imprime "ok".

**F2. Dedupe entre portales (≈45 min)**
- ✅ `check_dedupe` cubre: misma propiedad en los dos portales → un grupo; dos aptos distintos del mismo edificio → separados. Reportar el % de ofertas agrupadas.

**F3. Capas y enriquecimiento geográfico (≈1,5 h)**
- `scripts/fetch_layers.py` (WFS de IDESC y Overpass a `data/layers`).
- Estrato oficial por manzana. Barrio y comuna con punto en polígono.
- Amenidades, riesgos y proyectos por oferta.
- ✅ Más del 95% de ofertas con coordenada exacta reciben barrio oficial. Spot-check de 10 ofertas a mano contra el geovisor de IDESC.

**F4. Puntaje v1 (≈2 h)**
- Comparables, C1 a C5, penalizaciones, confianza y upside.
- `zone_stats` diario.
- `config.yaml` con los pesos.
- ✅ `check_score` con casos sintéticos: la oferta barata en una buena zona queda arriba; la oferta en zona no mitigable queda abajo y con su razón. El top 20 real se revisa a mano y se anotan las sorpresas en la bitácora.

**F5. Export y web (≈3 h)**
- Export de JSON y shards.
- Explorar (lista y mapa con Mapbox), filtros, detalle, favoritos, Metodología.
- Antes de esta fase, segunda ronda en Mobbin y carga de las skills de diseño.
- ✅ Funciona en desktop y en un viewport de 390 px. Carga en menos de 3 s. Sin errores de consola.

**F6. Automatización y deploy (≈45 min)**
- `scrape.yml`: cron `0 5 * * *` (full) y `0 11,17,23 * * *` (incremental, en UTC).
- Probe desde Actions.
- Proyecto en Vercel con `noindex`.
- Restringir el token de Mapbox por URL.
- ✅ Dos corridas en Actions exitosas y el sitio en línea con datos frescos.

**F7. Refinamiento (resto del día)**
- Modelo hedónico v2 con validación espacial.
- Hash de fotos en el dedupe.
- Página de Zonas.
- Heatmap.
- Detección de rebajas y republicadas.
- Pulido de la UI con `impeccable` y `break-ui` (peor caso de datos).

**F8. Calibración (continua, a partir de 2 a 4 semanas de datos)**
- Backtest proxy (sección 6.5) y ajuste de pesos.

## 12. Fuera de alcance (por ahora)

Alertas, multiusuario o login, sincronización de favoritos, otras ciudades, arriendos como producto, proyectos sobre planos y lotes, y cualquier servicio de pago.

## 13. Bitácora

| Fecha | Fase | Nota |
|---|---|---|
| 2026-11-14 | Plan | Volumen medido: FR ~10.850 y M2 ~9.980 (estratos 2 a 6). WFS de IDESC verificado. datos.cali.gov.co caído |
| 2026-11-14 | T12 ✅ | Actions: full a las 00:00 COT, incremental a las 06, 12 y 18, y `workflow_dispatch` con modo `pipeline`. La DB (sin tablas derivadas, gz ~15 MB) y `web-data.tar.gz` van como assets del release `db`. Cada corrida commitea solo `data/last_run.json`, lo que dispara Vercel; el build baja el tarball. Primera corrida de pipeline en Actions OK (dedupe → export en 35 s). Sitio en https://sic-r7k2.vercel.app. Token desde la env `MAPBOX_PK` (la `MAPBOX_TOKEN` quedó mal cargada y borrarla requiere aprobación humana). **Manual pendiente**: restringir el token por URL en el dashboard de Mapbox |
| 2026-11-14 | T6 fix | Con datos reales el union-find encadenaba (grupos de hasta 684). Se pasó a complete-linkage best-first, con máximo 6 por grupo y 2 activas por portal; las coincidencias no exactas exigen ±2% y el barrio "Na" cuenta como vacío. Resultado: 36% agrupado (2.123 pares FR+M2, grupo máximo de 4). Revisión manual de 10 grupos entre portales: todos son el mismo inmueble |
| 2026-11-14 | T8 ✅ | Primera corrida real: FR 10.519 y M2 9.722 (FR 75 min, M2 18 min). Puntaje: 85% de los grupos de venta activos tienen puntaje, en 4 s; 4.340 ranqueables. Filtro de outliers en dos pasadas (z_MAD > 4). Máximo 2 comparables por punto de ~15 m para que un edificio o el centroide de un barrio no domine; los inmuebles de más de 15 años no usan comparables de 8 años o menos. Pendientes: el upside sale inflado cuando la antigüedad falta o está mal (la v2 hedónica lo corrige); 1.210 grupos sin ubicación. La DB local pesa 223 MB con las tablas derivadas; en el release se guarda sin ellas y comprimida (15 MB). Los datos web (73 MB) también van al release y Vercel los descarga al compilar |
| 2026-11-14 | T9 ✅ | `export.py` escribe en un directorio temporal y lo renombra sobre `web/data` (los shards viejos desaparecen). Exporta las canónicas de venta con coordenadas; `meta.counts` incluye `no_coords`. `check_export` ok. Se escribió en paralelo con T8 contra el formato acordado de `listing_score` |
| 2026-11-14 | T7 ✅ | `geo.py`: 25k sintéticas en ~12 s (STRtree + kNN en numpy). El punto efectivo queda en `listing_geo.lat/lng`; la coordenada del portal no se toca, y un punto degradado guarda la original en `geo_note`. Estrato por manzana a ≤ 30 m y respaldo por barrio. Ladera: proxy de pendiente ≥ 25% (`pendientes.geojson`). Homicidios: ventana de 24 meses que termina en el último dato de la capa (2025-06). `check_geo` ok |
| 2026-11-14 | T11 ✅ | `metodologia.html`, `zonas.html` (coropleta y ranking, enlace `#barrio=`), control de capas en Explorar (heatmap, inundación, renovación, troncales y tren) y banner de corridas con error. `scripts/copy_web_layers.py` deja 0,5 MB. Contrato de `meta.runs`: `{at, source, mode, fetched, new, removed, errors, ok}` |
| 2026-11-14 | T10 ✅ | Explorar, detalle y favoritos en JS vanilla con Mapbox GL 3.9.4: clusters, pins con precio, rampa viridis, hoja inferior en móvil. Probado en desktop y en 390 px con datos de prueba (`web/dev/make_fixture.py`), sin errores de consola. Con 9k ofertas, filtrar toma 10–13 ms. Faltan: capas opcionales del mapa y `barrios.geojson` (T11), filtros en la URL y restringir el token de Mapbox por dominio (manual) |
| 2026-11-14 | T5 ✅ | 22 de 23 capas (~10 MB). `movimientos_masa` da 401 en IDESC: como proxy de ladera se usa la pendiente (`bcs_pendientes_porcentaje`). Estrato por manzana con la llave `estidterre[:8]`, que cubre 12.583 manzanas; respaldo modal por barrio. 16 malls ancla desde OSM. GTFS del MIO: 60 estaciones y 1.988 paradas |
| 2026-11-14 | T3 ✅ | M2: sin tope de profundidad (5.938 OK). El orden por defecto es aleatorio: se ordena por precio ascendente (`sortField=mvalorventa`, `sortOrder=asc`) para que la paginación sea estable. **No hay orden por fecha ni fecha de publicación**, así que el incremental de M2 hace un barrido completo (~205 requests, ~17 min). Aparece basura de precio (arriendos publicados como venta): el puntaje debe filtrar precio/m² absurdo |
| 2026-11-14 | T2 ✅ | FR: tope de ~6.124 resultados por búsqueda, así que se parte por estrato (una URL por operación × tipo × estrato). Orden `ordenListado=3` (más recientes). Proyectos: `isProject`, `isProjectUnit`, `project` o `/proyectos-vivienda`. `pointType=True` es una coordenada geocodificada desde la dirección escrita y a veces cae lejos del barrio: se trata como aproximada, y T7 la contrasta con el polígono del barrio. Barrido completo ≈ 520 páginas |
| 2026-11-14 | T6 ✅ | `dedupe.py`: buckets por operación, tipo y habitaciones, ventana de precio ±5%, union-find, republicadas ≤ 60 días, partición de grupos > 6. API `canonical_ids` y `group_first_seen`. 25k sintéticas en 0,3 s |
| 2026-11-14 | T4 ✅ | `db.py`, `main.py` (full/incremental, drop guard, pipeline vía importlib), `check_upsert` ok. Pendiente: detección de republicadas (va a T6); VACUUM en el workflow (T12) |
| 2026-11-14 | T1 ✅ | Esqueleto, `.venv` (uv, py3.12), `scraper/net.py` (Http), `docs/data-contract.md`. Repo `sic-r7k2` público. Arranca /loop |

## 14. Pendientes por verificar
- Orden "más recientes" y límite de paginación en FR y M2.
- Si IDESC expone el valor de terreno por zona homogénea económica (`zona_econ1`). Si existe, es una señal oficial de valor del suelo.
- Si las IPs de GitHub Actions pasan Incapsula (M2).

## 15. Ejecución con /loop y subagentes

Desde el 2026-11-14 no se trabaja por fases secuenciales sino con **/loop**. En cada iteración el orquestador:
1. Lee este backlog y la bitácora.
2. Toma **todas las tareas desbloqueadas** (sus dependencias ya están en ✅) y las lanza **en paralelo**. Cada subagente trabaja en su propio worktree o en archivos disjuntos.
3. Revisa lo que entrega cada subagente, corre los checks, integra, hace commit y push, marca ✅ y anota en la bitácora.
4. Si no queda nada desbloqueado o todo está en ✅, termina el loop. Si todo está en ✅, cierra con un reporte final.

Asignación de modelo: **opus** para tareas de diseño o algoritmos (puntaje, dedupe, UI principal); **sonnet** para tareas mecánicas o bien especificadas (portar código, descargar capas, workflows, export).

| # | Tarea | Depende de | Modelo | Estado |
|---|---|---|---|---|
| T1 | Repo, estructura, `config.yaml`, `requirements.txt`, `.gitignore`, `vercel.json`, `docs/data-contract.md` | — | (orquestador) | ✅ |
| T2 | Fuente Finca Raíz: Cali completa, venta y arriendo, estratos 2 a 6, sin proyectos, orden "recientes", límite de paginación | T1 | sonnet | ✅ |
| T3 | Fuente Metrocuadrado (ídem) | T1 | sonnet | ✅ |
| T4 | `db.py`: esquema SQLite, upsert, `price_change`, retiradas con protección, `run`; `main.py` en modos full e incremental; `check_upsert` | T1 | opus | ✅ |
| T5 | `scripts/fetch_layers.py`: capas IDESC, Overpass, GTFS del MIO y `config/anchors.yaml` a `data/layers/` | T1 | sonnet | ✅ |
| T6 | Dedupe intra y entre portales + `check_dedupe` | T4 | opus | ✅ |
| T7 | `geo.py`: barrio, comuna, estrato por manzana, amenidades, riesgos y proyectos | T4, T5 | opus | ✅ |
| T8 | `score.py` + `zones.py`: comparables, C1 a C5, penalizaciones, confianza + `check_score` | T6, T7 | opus | ✅ |
| T9 | `export.py`: `listings.json`, shards de detalle, `zones.json`, `meta.json` | T8 | sonnet | ✅ |
| T10 | Web: Explorar (lista y mapa Mapbox), filtros, detalle, favoritos; referencias de Mobbin | T9 (contrato de datos; puede arrancar con un fixture) | opus | ✅ |
| T11 | Web: Metodología y Zonas (coropleta) | T10 | sonnet | ✅ |
| T12 | GitHub Actions (cron full + incremental, probe) y deploy en Vercel; restringir el token de Mapbox | T4, T9 | sonnet | ✅ |
| T13 | Primera corrida full real, revisión del top 20, ajustes | T8, T12 | opus | ⬜ |
| T14 | QA de la UI (`break-ui`, móvil 390 px, consola limpia) y pulido | T10, T11 | sonnet | ⬜ |
| T15 | v2: modelo hedónico con validación espacial; pesos de amenidades calibrados | T13 | opus | ⬜ |
| T16 | Hash de fotos en el dedupe y detección de republicadas | T13 | sonnet | ⬜ |

Para que T10 no espere a T9, el contrato de `listings.json` se define en T4 (`docs/data-contract.md`) y T10 arranca con un fixture.

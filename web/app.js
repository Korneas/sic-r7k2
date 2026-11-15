/* sic: explorar + detalle. Vanilla JS, sin build. Lee web/data/ (docs/data-contract.md §3). */
(function () {
  'use strict';

  // ---------- utilidades ----------
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var NF = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
  var NF1 = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  var DATEF = new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'America/Bogota' });
  var DTF = new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'America/Bogota' });
  var MOBILE = window.matchMedia('(max-width: 768px)');
  var DARK = window.matchMedia('(prefers-color-scheme: dark)');
  var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function debounce(fn, ms) {
    var t; return function () { var a = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(null, a); }, ms); };
  }
  /* COP abreviado: $320M, $1.250M; precio/m²: $4,2M. */
  function cop(p) {
    if (p == null) return '—';
    if (p >= 1e12) return '$' + NF1.format(p / 1e12) + ' bill.';
    if (p >= 1e6) return '$' + NF.format(Math.round(p / 1e6)) + 'M';
    return '$' + NF.format(p);
  }
  function copM1(p) { return p == null ? '—' : '$' + NF1.format(p / 1e6) + 'M'; }
  function copFull(p) { return p == null ? '—' : '$' + NF.format(p); }
  function pct(x, signed) {
    if (x == null) return '—';
    var v = Math.round(x * 100);
    return (signed && v > 0 ? '+' : '') + (v < 0 ? '−' + Math.abs(v) : v) + '%';
  }
  function meters(m) { return m >= 1000 ? NF1.format(m / 1000) + ' km' : NF.format(m) + ' m'; }
  function walkMin(m) { return Math.max(1, Math.round(m * 1.3 / 80)); }
  function fmtDate(iso) { var d = new Date(iso + 'T12:00:00'); return isNaN(d) ? iso : DATEF.format(d); }

  var BINS = [[85, 4, 'Excelente'], [70, 3, 'Muy bueno'], [55, 2, 'Bueno'], [40, 1, 'Regular'], [-1, 0, 'Bajo']];
  function bin(s) { if (s == null) return 'n'; for (var i = 0; i < BINS.length; i++) if (s >= BINS[i][0]) return BINS[i][1]; return 0; }
  function binLabel(s) { if (s == null) return 'No es oportunidad'; for (var i = 0; i < BINS.length; i++) if (s >= BINS[i][0]) return BINS[i][2]; return 'Bajo'; }
  function scoreBadge(s) {
    return '<span class="score s' + bin(s) + '">' + (s == null ? '<b>—</b>' : '<b>' + Math.round(s) + '</b>') + esc(binLabel(s)) + '</span>';
  }

  var COMP = [
    { k: 'Descuento', w: 0.35, note: 'precio/m² frente a comparables' },
    { k: 'Potencial de reventa', w: 0.25, note: 'upside hasta el P75 en buen estado' },
    { k: 'Valorización zona', w: 0.15, note: 'tendencia y proyectos cercanos' },
    { k: 'Amenidades', w: 0.15, note: 'MIO, comercio, parques, salud…' },
    { k: 'Liquidez', w: 0.10, note: 'qué tan rápido se vende en la zona' }
  ];
  var PEN_SHORT = { inund_nm: 'Inundación', inund: 'Inundable', masa: 'Ladera', homic: 'Homicidios', homic75: 'Homicidios', mejora: 'Mejoramiento', ruido: 'Ruido', legal: 'Legal', aprox: 'Ubic. aprox.' };
  var HIDE_TAGS = { no_oportunidad: 1 };
  var TAGS = { nueva: 'Nueva', rebaja: 'Rebaja', remodelar: '🔨 Para remodelar', legal: '⚖️ Legal', sospechoso: 'Dato sospechoso', mixto: 'Uso mixto/renta' };
  var UP_CAP = 0.8; // config score.arv.upside_cap: el upside se guarda con tope
  function upTxt(x) { return (x >= UP_CAP - 0.0005 ? '≥ ' : '') + pct(x, true); }
  var AMEN = { mio: 'MIO troncal', mio_stop: 'Parada MIO', mall: 'Centros comerciales', super: 'Supermercados', park: 'Parques', health: 'Clínicas y hospitales', edu: 'Colegios y universidades', food: 'Restaurantes y cafés', bank: 'Bancos y droguerías', gym: 'Deporte' };
  var PROJ = { renov: 'Renovación urbana', plan_parcial: 'Plan parcial', mio_proy: 'Troncal MIO proyectada', corredor_verde: 'Corredor Verde', tren: 'Tren de cercanías', plusvalia: 'Plusvalía' };

  // ---------- favoritos ----------
  var FAV_KEY = 'sic.favs';
  var favs = new Set();
  try { favs = new Set(JSON.parse(localStorage.getItem(FAV_KEY) || '[]')); } catch (e) { favs = new Set(); }
  function saveFavs() { try { localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(favs))); } catch (e) { /* modo privado */ } }
  function toggleFav(id) {
    if (favs.has(id)) favs.delete(id); else favs.add(id);
    saveFavs();
    document.querySelectorAll('.fav[data-id="' + CSS.escape(id) + '"]').forEach(function (b) { setFavBtn(b, id); });
    if (current && current.id === id) setFavBtn($('#detailFav'), id);
    if (state.f.favs) apply();
  }
  function setFavBtn(b, id) {
    var on = favs.has(id);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.setAttribute('aria-label', on ? 'Quitar de favoritos' : 'Guardar en favoritos');
  }

  // ---------- estado ----------
  var all = [], byId = new Map(), zones = {}, meta = {}, today = new Date();
  var filtered = [], visible = [], rendered = 0, PAGE = 50;
  var state = { f: {}, sort: 'score', area: true };
  var map = null, mapReady = false, hotId = null, current = null, miniMap = null;

  // ---------- filtros ----------
  var FILTERS = [
    { k: 'price', label: 'Precio', type: 'range', field: 'p', scale: 1e6, unit: 'M', step: 10,
      presets: [['Hasta $300M', null, 300], ['$300M–$700M', 300, 700], ['$700M–$1.200M', 700, 1200], ['Más de $1.200M', 1200, null]],
      show: function (v) { return rangeText(v, function (x) { return '$' + NF.format(x) + 'M'; }); } },
    { k: 'pm', label: 'Precio/m²', type: 'range', field: 'pm', scale: 1e6, unit: 'M/m²', step: 0.1,
      presets: [['Hasta $3M', null, 3], ['$3M–$5M', 3, 5], ['Más de $5M', 5, null]],
      show: function (v) { return rangeText(v, function (x) { return '$' + NF1.format(x) + 'M'; }); } },
    { k: 'area', label: 'Área', type: 'range', field: 'a', scale: 1, unit: 'm²', step: 5,
      presets: [['Hasta 60 m²', null, 60], ['60–100 m²', 60, 100], ['100–180 m²', 100, 180], ['Más de 180 m²', 180, null]],
      show: function (v) { return rangeText(v, function (x) { return NF.format(x) + ' m²'; }); } },
    { k: 'beds', label: 'Habitaciones', type: 'min', field: 'b', opts: [1, 2, 3, 4], show: function (v) { return v + '+ hab.'; } },
    { k: 'baths', label: 'Baños', type: 'min', field: 'ba', opts: [1, 2, 3, 4], show: function (v) { return v + '+ baños'; } },
    { k: 'park', label: 'Parqueadero', type: 'min', field: 'pk', opts: [1, 2, 3], show: function (v) { return v + '+ parq.'; } },
    { k: 'estrato', label: 'Estrato', type: 'multi', field: 'e', opts: [[1, '1'], [2, '2'], [3, '3'], [4, '4'], [5, '5'], [6, '6']],
      show: function (v) { return 'Estrato ' + v.slice().sort().join(', '); } },
    { k: 'tipo', label: 'Tipo', type: 'multi', field: 't', opts: [['a', 'Apartamento'], ['c', 'Casa']],
      show: function (v) { return v.length === 2 ? 'Apto y casa' : (v[0] === 'a' ? 'Apartamento' : 'Casa'); } },
    { k: 'zona', label: 'Comuna o barrio', type: 'zona',
      show: function (v) {
        var n = v.cm.length + v.br.length;
        if (n === 1) return v.cm.length ? (v.cm[0] ? 'Comuna ' + v.cm[0] : 'Fuera de Cali') : (zones[v.br[0]] ? zones[v.br[0]].name : v.br[0]);
        return n + ' zonas';
      } },
    { k: 'score', label: 'Puntaje mín.', type: 'slider', field: 's', min: 0, max: 95, step: 5, fmt: function (v) { return String(v); },
      show: function (v) { return 'Puntaje ≥ ' + v; } },
    { k: 'disc', label: 'Descuento mín.', type: 'slider', field: 'd', min: 0, max: 50, step: 5, pct: true, fmt: function (v) { return v + '%'; },
      show: function (v) { return 'Descuento ≥ ' + v + '%'; } },
    { k: 'up', label: 'Upside mín.', type: 'slider', field: 'up', min: 0, max: 80, step: 5, pct: true, fmt: function (v) { return '+' + v + '%'; },
      show: function (v) { return 'Upside ≥ ' + v + '%'; } },
    { k: 'dom', label: 'Días publicada', type: 'max', field: 'dom', opts: [[7, '≤ 7 días'], [30, '≤ 30 días'], [90, '≤ 90 días']],
      show: function (v) { return 'Publicada ≤ ' + v + ' días'; } },
    { sep: true },
    { k: 'remodelar', label: '🔨 Para remodelar', type: 'toggle' },
    { k: 'rebaja', label: 'Con rebaja', type: 'toggle' },
    { k: 'nueva', label: 'Nueva < 48 h', type: 'toggle' },
    { k: 'nopen', label: 'Sin penalizaciones', type: 'toggle' },
    { k: 'favs', label: '❤ Solo favoritos', type: 'toggle' }
  ];
  var FBY = {}; FILTERS.forEach(function (f) { if (f.k) FBY[f.k] = f; });

  function rangeText(v, f) {
    if (v[0] != null && v[1] != null) return f(v[0]) + '–' + f(v[1]);
    if (v[0] != null) return 'Desde ' + f(v[0]);
    return 'Hasta ' + f(v[1]);
  }
  function isActive(k) {
    var v = state.f[k];
    if (v == null || v === false) return false;
    if (Array.isArray(v)) return v.length > 0 && !(FBY[k].type === 'range' && v[0] == null && v[1] == null);
    if (FBY[k].type === 'zona') return v.cm.length + v.br.length > 0;
    return true;
  }
  function nPen(l) { var n = 0; for (var i = 0; i < l.pen.length; i++) if (l.pen[i].pts > 0) n++; return n; }

  function predicate(k) {
    var f = FBY[k], v = state.f[k];
    switch (f.type) {
      case 'range':
        var lo = v[0] != null ? v[0] * f.scale : -Infinity, hi = v[1] != null ? v[1] * f.scale : Infinity, fld = f.field;
        return function (l) { var x = l[fld]; return x != null && x >= lo && x <= hi; };
      case 'min':
        return function (l) { var x = l[f.field]; return x != null && x >= v; };
      case 'max':
        return function (l) { var x = l[f.field]; return x != null && x <= v; };
      case 'multi':
        var set = new Set(v.map(String));
        return function (l) { return set.has(String(l[f.field])); };
      case 'zona':
        var cms = new Set(v.cm), brs = new Set(v.br);
        return function (l) { return cms.has(l.cm == null ? 0 : l.cm) || brs.has(l.brc); };
      case 'slider':
        var th = f.pct ? v / 100 : v;
        return function (l) { var x = l[f.field]; return x != null && x >= th; };
      case 'toggle':
        if (k === 'remodelar') return function (l) { return l._tags.remodelar; };
        if (k === 'rebaja') return function (l) { return l._tags.rebaja; };
        if (k === 'nueva') return function (l) { return l._tags.nueva; };
        if (k === 'nopen') return function (l) { return l._npen === 0; };
        if (k === 'favs') return function (l) { return favs.has(l.id); };
    }
    return function () { return true; };
  }
  function activeKeys() { return Object.keys(state.f).filter(isActive); }
  function runFilters(keys) {
    var preds = keys.map(predicate);
    var out = [];
    outer: for (var i = 0; i < all.length; i++) {
      var l = all[i];
      for (var j = 0; j < preds.length; j++) if (!preds[j](l)) continue outer;
      out.push(l);
    }
    return out;
  }

  var SORTS = {
    score: function (a, b) { return nz(b.s, -1) - nz(a.s, -1) || nz(b.d, -9) - nz(a.d, -9); },
    disc: function (a, b) { return nz(b.d, -9) - nz(a.d, -9); },
    up: function (a, b) { return nz(b.up, -9) - nz(a.up, -9); },
    price: function (a, b) { return a.p - b.p; },
    pm: function (a, b) { return nz(a.pm, 1e12) - nz(b.pm, 1e12); },
    'new': function (a, b) { return a.fs < b.fs ? 1 : a.fs > b.fs ? -1 : nz(b.s, -1) - nz(a.s, -1); }
  };
  function nz(x, d) { return x == null ? d : x; }

  function apply(fromPanel) {
    filtered = runFilters(activeKeys());
    filtered.sort(SORTS[state.sort]);
    updateMapData();
    updateVisible(true);
    if (panelOpen && !fromPanel) renderPanel();
    renderChips();
  }
  var applySoon = debounce(function () { apply(true); }, 140);

  function inBounds() {
    if (!state.area || !mapReady) return null;
    var b = map.getBounds();
    return { s: b.getSouth(), n: b.getNorth(), w: b.getWest(), e: b.getEast() };
  }
  function updateVisible(stagger) {
    var bb = inBounds();
    visible = bb ? filtered.filter(function (l) { return l._geo && l.lat >= bb.s && l.lat <= bb.n && l.lng >= bb.w && l.lng <= bb.e; }) : filtered;
    renderCount(bb);
    renderList(true, stagger);
    if (panelOpen) doneTxt();
  }

  // ---------- barra: favoritos + filtros activos ----------
  var fBtn = $('#fBtn'), fBtnN = $('#fBtnN');
  function chipTxt(k) { var f = FBY[k]; return f.type === 'toggle' ? f.label : f.show(state.f[k]); }
  function renderChips() {
    var box = $('#chips');
    var keys = activeKeys().filter(function (k) { return k !== 'favs'; });
    var html = '<button type="button" class="chip chip-fav' + (state.f.favs ? ' on' : '') + '" data-k="favs" aria-pressed="' + !!state.f.favs + '">' +
      '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 17s-6.5-3.9-6.5-8.6A3.6 3.6 0 0 1 10 6a3.6 3.6 0 0 1 6.5 2.4C16.5 13.1 10 17 10 17z"/></svg>Favoritos</button>' +
      keys.map(function (k) {
        return '<button type="button" class="chip on" data-rm="' + k + '" aria-label="Quitar filtro: ' + esc(chipTxt(k)) + '"><span class="chip-val">' + esc(chipTxt(k)) + '</span><svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5"/></svg></button>';
      }).join('') +
      (keys.length > 1 ? '<button type="button" class="chip chip-clear" id="clearAll">Limpiar</button>' : '');
    var ae = document.activeElement, inBox = ae && box.contains(ae);
    var focusSel = inBox ? (ae.id ? '#' + ae.id : ae.getAttribute('data-k') ? '[data-k="' + ae.getAttribute('data-k') + '"]' : null) : null;
    var sl = box.scrollLeft;
    box.innerHTML = html;
    box.scrollLeft = sl;
    if (inBox) { var el = focusSel && box.querySelector(focusSel); (el || fBtn).focus(); }
    var n = activeKeys().length;
    fBtnN.hidden = !n; fBtnN.textContent = n;
    fBtn.classList.toggle('on', n > 0);
    fpBody.querySelectorAll('[data-sec]').forEach(function (sec) { sec.classList.toggle('on', !!FBY[sec.getAttribute('data-sec')] && isActive(sec.getAttribute('data-sec'))); });
    doneTxt();
  }
  function doneTxt() { $('#fpDone').textContent = 'Ver ' + NF.format(visible.length) + (visible.length === 1 ? ' oferta' : ' ofertas'); }

  $('#chips').addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'clearAll') state.f = {};
    else if (b.hasAttribute('data-rm')) delete state.f[b.getAttribute('data-rm')];
    else if (b.getAttribute('data-k') === 'favs') state.f.favs = !state.f.favs;
    apply();
  });

  // ---------- panel de filtros (dropdown en escritorio, hoja inferior en móvil) ----------
  var PANEL = ['tipo', 'price', 'zona', 'score', 'disc', 'up', 'estrato', 'area', 'pm', 'beds', 'baths', 'park', 'dom'];
  var fpanel = $('#fpanel'), fpBody = $('#fpBody'), panelOpen = false, scrim = $('#scrim');
  function renderPanel() {
    var sy = fpBody.scrollTop;
    fpBody.innerHTML = PANEL.map(function (k) {
      var f = FBY[k];
      return '<section class="fsec' + (isActive(k) ? ' on' : '') + '" data-sec="' + k + '"><div class="fsec-h"><h3>' + esc(f.label) + '</h3>' +
        '<button type="button" class="fsec-x" data-act="clear">Quitar</button></div>' + popBody(f) + '</section>';
    }).join('') + '<section class="fsec" data-sec="_t"><div class="fsec-h"><h3>Más</h3></div><div class="checks">' +
      FILTERS.filter(function (f) { return f.type === 'toggle' && f.k !== 'favs'; }).map(function (f) {
        return '<label class="check"><input type="checkbox" data-toggle="' + f.k + '"' + (state.f[f.k] ? ' checked' : '') + '><span>' + esc(f.label) + '</span></label>';
      }).join('') + '</div></section>';
    fpBody.scrollTop = sy;
  }
  function positionPanel() {
    document.documentElement.style.setProperty('--fp-top', Math.round(fBtn.getBoundingClientRect().bottom + 8) + 'px');
  }
  function openPanel() {
    panelOpen = true;
    renderPanel(); positionPanel(); doneTxt();
    fpanel.inert = false; fpanel.classList.add('open');
    document.body.classList.add('fp-open');
    fBtn.setAttribute('aria-expanded', 'true');
    fpanel.querySelector('.icon-btn').focus({ preventScroll: true });
  }
  function closePanel(restore) {
    if (!panelOpen) return;
    panelOpen = false;
    fpanel.classList.remove('open'); fpanel.inert = true;
    document.body.classList.remove('fp-open');
    fBtn.setAttribute('aria-expanded', 'false');
    if (restore) fBtn.focus();
  }
  fBtn.addEventListener('click', function () { if (panelOpen) closePanel(); else openPanel(); });
  scrim.addEventListener('click', function () { if (panelOpen) closePanel(); else closeDetail(); });

  function popBody(f) {
    var v = state.f[f.k];
    if (f.type === 'range') {
      var lo = v ? v[0] : null, hi = v ? v[1] : null;
      return '<div class="range">' +
        '<label>Mínimo<span class="input-unit"><input type="number" inputmode="decimal" min="0" step="' + f.step + '" data-r="0" value="' + (lo == null ? '' : lo) + '" placeholder="Sin mínimo"><span>' + esc(f.unit) + '</span></span></label>' +
        '<span class="range-dash">–</span>' +
        '<label>Máximo<span class="input-unit"><input type="number" inputmode="decimal" min="0" step="' + f.step + '" data-r="1" value="' + (hi == null ? '' : hi) + '" placeholder="Sin máximo"><span>' + esc(f.unit) + '</span></span></label>' +
        '</div><div class="presets">' + f.presets.map(function (p, i) {
          var on = v && v[0] === p[1] && v[1] === p[2];
          return '<button type="button" class="preset" data-preset="' + i + '" aria-pressed="' + !!on + '">' + esc(p[0]) + '</button>';
        }).join('') + '</div>' + (f.k === 'pm' || f.k === 'area' ? '<p class="pop-hint">Las ofertas sin área se ocultan con este filtro.</p>' : '');
    }
    if (f.type === 'min' || f.type === 'max') {
      var opts = f.type === 'min' ? [[null, 'Todas']].concat(f.opts.map(function (o) { return [o, o + '+']; })) : [[null, 'Todas']].concat(f.opts);
      return '<div class="seg" role="group" aria-label="' + esc(f.label) + '">' + opts.map(function (o) {
        return '<button type="button" data-v="' + (o[0] == null ? '' : o[0]) + '" aria-pressed="' + ((v == null && o[0] == null) || v === o[0]) + '">' + esc(o[1]) + '</button>';
      }).join('') + '</div>';
    }
    if (f.type === 'multi') {
      var cnt = countBy(f.field);
      return '<div class="checks">' + f.opts.map(function (o) {
        var on = v && v.indexOf(o[0]) >= 0;
        return '<label class="check"><input type="checkbox" data-v="' + o[0] + '"' + (on ? ' checked' : '') + '><span>' + esc(f.k === 'estrato' ? 'Estrato ' + o[1] : o[1]) + '</span><small>' + NF.format(cnt[o[0]] || 0) + '</small></label>';
      }).join('') + '</div>';
    }
    if (f.type === 'slider') {
      var val = v == null ? f.min : v;
      return '<div class="slider-row"><input type="range" min="' + f.min + '" max="' + f.max + '" step="' + f.step + '" value="' + val + '" aria-label="' + esc(f.label) + '"><output>' + esc(f.fmt(val)) + '</output></div>' +
        (f.k === 'score' ? '<p class="pop-hint">Las ofertas sin puntaje (no ranqueables) se ocultan al subir el mínimo.</p>' : '');
    }
    if (f.type === 'zona') {
      return '<input type="search" placeholder="Buscar comuna o barrio" aria-label="Buscar comuna o barrio" data-zsearch style="width:100%">' +
        '<div class="zona-list" data-zlist>' + zonaList('') + '</div>';
    }
    return '';
  }
  function countBy(field) { var c = {}; all.forEach(function (l) { c[l[field]] = (c[l[field]] || 0) + 1; }); return c; }
  function normTxt(s) { return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
  var comunas = [];
  function zonaList(q) {
    var v = state.f.zona || { cm: [], br: [] };
    var nq = normTxt(q);
    var html = '';
    comunas.forEach(function (c) {
      var cmMatch = !nq || ((c.cm ? 'comuna ' + c.cm : 'fuera rural jamundi') + '').indexOf(nq) >= 0;
      var brs = !c.cm ? [] : c.barrios.filter(function (b) { return cmMatch || normTxt(b.name).indexOf(nq) >= 0; });
      if (!cmMatch && !brs.length) return;
      html += '<label class="check zona-group"><input type="checkbox" data-cm="' + c.cm + '"' + (v.cm.indexOf(c.cm) >= 0 ? ' checked' : '') + '><span>' + (c.cm ? 'Comuna ' + c.cm : 'Fuera de las comunas (rural, Jamundí…)') + '</span><small>' + NF.format(c.n) + '</small></label>';
      brs.forEach(function (b) {
        html += '<label class="check zona-br"><input type="checkbox" data-br="' + esc(b.code) + '"' + (v.br.indexOf(b.code) >= 0 ? ' checked' : '') + '><span title="' + esc(b.name) + '">' + esc(b.name) + '</span><small>' + NF.format(b.n) + '</small></label>';
      });
    });
    return html || '<p class="pop-hint">Ninguna comuna o barrio coincide con «' + esc(q) + '».</p>';
  }

  fpanel.addEventListener('click', function (e) {
    var act = e.target.closest('[data-act]');
    var sec = e.target.closest('[data-sec]'), f = sec && FBY[sec.getAttribute('data-sec')];
    if (act) {
      var a = act.getAttribute('data-act');
      if (a === 'done') closePanel(true);
      else if (a === 'clearall') { state.f = {}; apply(); }
      else if (a === 'clear' && f) {
        delete state.f[f.k]; apply();
        var nf = fpBody.querySelector('[data-sec="' + f.k + '"] input, [data-sec="' + f.k + '"] button[aria-pressed]'); if (nf) nf.focus();
      }
      return;
    }
    if (!f) return;
    var pre = e.target.closest('[data-preset]');
    if (pre) {
      var p = f.presets[+pre.getAttribute('data-preset')];
      state.f[f.k] = [p[1], p[2]];
      sec.querySelector('[data-r="0"]').value = p[1] == null ? '' : p[1];
      sec.querySelector('[data-r="1"]').value = p[2] == null ? '' : p[2];
      sec.querySelectorAll('.preset').forEach(function (b) { b.setAttribute('aria-pressed', b === pre); });
      apply(true); return;
    }
    var sb = e.target.closest('.seg button');
    if (sb) {
      var raw = sb.getAttribute('data-v');
      if (raw === '') delete state.f[f.k]; else state.f[f.k] = +raw;
      sec.querySelectorAll('.seg button').forEach(function (b) { b.setAttribute('aria-pressed', b === sb); });
      apply(true);
    }
  });
  fpanel.addEventListener('input', function (e) {
    var t = e.target;
    if (t.hasAttribute('data-toggle')) { var tk = t.getAttribute('data-toggle'); if (t.checked) state.f[tk] = true; else delete state.f[tk]; apply(true); return; }
    var sec = t.closest('[data-sec]'), f = sec && FBY[sec.getAttribute('data-sec')];
    if (!f) return;
    if (f.type === 'range') {
      var a = sec.querySelector('[data-r="0"]').value, b = sec.querySelector('[data-r="1"]').value;
      var lo = a === '' ? null : +a, hi = b === '' ? null : +b;
      if (lo == null && hi == null) delete state.f[f.k]; else state.f[f.k] = [lo, hi];
      sec.querySelectorAll('.preset').forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
      applySoon();
    } else if (f.type === 'slider') {
      var v = +t.value;
      sec.querySelector('output').textContent = f.fmt(v);
      if (v <= f.min) delete state.f[f.k]; else state.f[f.k] = v;
      applySoon();
    } else if (f.type === 'multi') {
      var vals = Array.prototype.map.call(sec.querySelectorAll('input:checked'), function (i) {
        var x = i.getAttribute('data-v'); return f.k === 'estrato' ? +x : x;
      });
      if (vals.length) state.f[f.k] = vals; else delete state.f[f.k];
      applySoon();
    } else if (f.type === 'zona') {
      if (t.hasAttribute('data-zsearch')) { sec.querySelector('[data-zlist]').innerHTML = zonaList(t.value); return; }
      var z = state.f.zona || { cm: [], br: [] };
      z = { cm: z.cm.slice(), br: z.br.slice() };
      if (t.hasAttribute('data-cm')) toggleIn(z.cm, +t.getAttribute('data-cm'), t.checked);
      if (t.hasAttribute('data-br')) toggleIn(z.br, t.getAttribute('data-br'), t.checked);
      if (z.cm.length + z.br.length) state.f.zona = z; else delete state.f.zona;
      applySoon();
    }
  });
  function toggleIn(arr, v, on) { var i = arr.indexOf(v); if (on && i < 0) arr.push(v); if (!on && i >= 0) arr.splice(i, 1); }
  document.addEventListener('pointerdown', function (e) {
    if (panelOpen && !fpanel.contains(e.target) && e.target !== scrim && !e.target.closest('#fBtn')) closePanel();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (panelOpen) { closePanel(true); return; }
    if (current) closeDetail();
  });
  // Acciones de teclado sin animación (Emil): se marca la modalidad de entrada.
  document.addEventListener('keydown', function (e) { if (e.key !== 'Shift') document.documentElement.classList.add('kbd'); }, true);
  document.addEventListener('pointerdown', function () { document.documentElement.classList.remove('kbd'); }, true);
  window.addEventListener('resize', debounce(function () { if (panelOpen) positionPanel(); layoutSheet(); }, 100));

  // ---------- conteo y lista ----------
  function renderCount(bb) {
    var c = $('#count');
    if (bb && filtered.length !== visible.length) {
      c.innerHTML = '<b>' + NF.format(visible.length) + '</b> en esta área de ' + NF.format(filtered.length);
    } else {
      c.innerHTML = '<b>' + NF.format(visible.length) + '</b> ' + (visible.length === 1 ? 'oferta' : 'ofertas');
    }
  }
  var listEl = $('#list');
  function renderList(reset, stagger) {
    if (reset) { listEl.innerHTML = ''; rendered = 0; $('#listScroll').scrollTop = 0; }
    var end = Math.min(visible.length, rendered + PAGE);
    var html = '';
    for (var i = rendered; i < end; i++) html += cardHTML(visible[i], i, stagger && i < 10);
    listEl.insertAdjacentHTML('beforeend', html);
    rendered = end;
    renderEmpty();
  }
  var nuevaShare = 0;
  function visTags(l) {
    return l.tags.filter(function (t) { return !HIDE_TAGS[t] && !(t === 'nueva' && nuevaShare > 0.8); });  // primera corrida: todo es "nueva"
  }
  function cardHTML(l, i, enter) {
    var photo = l.img
      ? '<img src="' + esc(l.img) + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" width="132" height="112" onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{className:\'no-photo\',textContent:\'Foto no disponible\'}))">'
      : '<div class="no-photo">Sin fotos</div>';
    var deltas = '';
    if (l.d != null) deltas += l.d > 0 ? '<span class="dchip">' + pct(-l.d) + ' vs zona</span>' : '<span class="dchip flat">' + pct(-l.d, true) + ' vs zona</span>';
    if (l.up != null && l.up > 0) deltas += '<span class="dchip">upside ' + upTxt(l.up) + '</span>';
    var meta = '<span>E' + (l.e || '?') + '</span>' +
      '<span>' + (l.a ? NF.format(l.a) + ' m²' : 'sin área') + '</span>' +
      (l.b != null ? '<span>' + l.b + ' hab</span>' : '') +
      (l.ba != null ? '<span>' + l.ba + (l.ba === 1 ? ' baño' : ' baños') + '</span>' : '') +
      (l.pk ? '<span>' + l.pk + ' parq</span>' : '<span>sin parq</span>');
    var badges = visTags(l).map(function (t) {
      var txt = TAGS[t] || t;
      if (t === 'rebaja' && l.pd != null) txt = 'Rebaja ' + pct(l.pd);
      return '<span class="tag tag-' + t + '">' + esc(txt) + '</span>';
    }).join('') + l.pen.map(function (p) {
      return '<span class="pen' + (p.pts ? '' : ' pen-0') + '" tabindex="0" data-tip="' + esc(p.why) + '" aria-label="' + esc((p.pts ? 'Penalización −' + p.pts + ': ' : '') + p.why) + '">' +
        (p.pts ? '−' + p.pts + ' ' : '') + esc(PEN_SHORT[p.k] || p.k) + '</span>';
    }).join('');
    return '<li class="card' + (current && current.id === l.id ? ' current' : '') + (enter ? ' in" style="--i:' + i : '') + '" data-id="' + esc(l.id) + '">' +
      '<a class="card-link" href="#' + encodeURIComponent(l.id) + '">' +
      '<div class="card-photo">' + photo + '<span class="rank" aria-label="Posición ' + (i + 1) + '">' + (i + 1) + '</span></div>' +
      '<div class="card-body">' +
      '<div class="card-row1"><div><div class="price">' + cop(l.p) + '</div>' +
      '<div class="pm">' + (l.t === 'c' ? 'Casa' : 'Apto') + ' · ' + (l.pm ? copM1(l.pm) + '/m²' : 'sin $/m²') + '</div></div>' + scoreBadge(l.s) + '</div>' +
      (deltas ? '<div class="deltas">' + deltas + '</div>' : '') +
      '<div class="br" title="' + esc(l.br) + '">' + esc(l.br || 'Barrio sin dato') + '</div>' +
      '<div class="meta">' + meta + '</div>' +
      (badges ? '<div class="badges">' + badges + '</div>' : '') +
      '</div></a>' +
      '<button type="button" class="fav" data-id="' + esc(l.id) + '" aria-pressed="' + favs.has(l.id) + '" aria-label="' + (favs.has(l.id) ? 'Quitar de favoritos' : 'Guardar en favoritos') + '">' +
      '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 17s-6.5-3.9-6.5-8.6A3.6 3.6 0 0 1 10 6a3.6 3.6 0 0 1 6.5 2.4C16.5 13.1 10 17 10 17z"/></svg></button>' +
      '</li>';
  }

  function renderEmpty() {
    var box = $('#empty');
    if (visible.length) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    var keys = activeKeys();
    if (filtered.length && state.area && mapReady) {
      box.innerHTML = '<h2>No hay ofertas en esta parte del mapa</h2><p>' + NF.format(filtered.length) + ' ofertas cumplen tus filtros en otras zonas de Cali.</p>' +
        '<div class="empty-actions"><button type="button" class="btn btn-primary" data-empty="fit">Ver todas en el mapa</button>' +
        '<button type="button" class="btn" data-empty="area">Desactivar «Buscar en esta área»</button></div>';
      return;
    }
    if (!keys.length) { box.innerHTML = '<h2>No hay ofertas cargadas</h2><p>El archivo de datos está vacío. Corre el export o el generador de prueba.</p>'; return; }
    // Para cada filtro activo: cuántas ofertas aparecerían si se quita.
    var opts = keys.map(function (k) {
      return { k: k, n: runFilters(keys.filter(function (x) { return x !== k; })).length };
    }).sort(function (a, b) { return b.n - a.n; });
    box.innerHTML = '<h2>Ninguna oferta cumple todos los filtros</h2><p>Quita uno para ver resultados:</p><div class="empty-actions">' +
      opts.map(function (o) {
        var f = FBY[o.k];
        var name = f.type === 'toggle' ? f.label : f.show(state.f[o.k]);
        return '<button type="button" class="btn" data-empty="rm" data-k="' + o.k + '">Quitar «' + esc(name) + '» <small>' + NF.format(o.n) + ' ofertas</small></button>';
      }).join('') + '<button type="button" class="btn btn-ghost" data-empty="all">Limpiar todos los filtros</button></div>';
  }
  $('#empty').addEventListener('click', function (e) {
    var b = e.target.closest('[data-empty]'); if (!b) return;
    var a = b.getAttribute('data-empty');
    if (a === 'rm') { delete state.f[b.getAttribute('data-k')]; apply(); }
    else if (a === 'all') { state.f = {}; apply(); }
    else if (a === 'area') { $('#areaToggle').checked = false; state.area = false; updateVisible(); }
    else if (a === 'fit') fitTo(filtered);
  });

  var io = new IntersectionObserver(function (ents) {
    if (ents[0].isIntersecting && rendered < visible.length) renderList(false);
  }, { root: $('#listScroll'), rootMargin: '600px' });
  io.observe($('#sentinel'));

  listEl.addEventListener('click', function (e) {
    var fb = e.target.closest('.fav');
    if (fb) { e.preventDefault(); toggleFav(fb.getAttribute('data-id')); }
  });
  listEl.addEventListener('mouseover', function (e) {
    var c = e.target.closest('.card'); if (!c) return;
    setHot(c.getAttribute('data-id'), 'list');
  });
  listEl.addEventListener('mouseleave', function () { setHot(null, 'list'); });
  listEl.addEventListener('focusin', function (e) {
    var c = e.target.closest('.card'); if (c) setHot(c.getAttribute('data-id'), 'list');
  });

  $('#sort').addEventListener('change', function (e) { state.sort = e.target.value; apply(); });
  $('#areaToggle').addEventListener('change', function (e) { state.area = e.target.checked; updateVisible(); });

  // Tooltip de penalizaciones (fuera de la tarjeta para que no se recorte).
  var tip = $('#tip');
  function showTip(el) {
    tip.textContent = el.getAttribute('data-tip'); tip.hidden = false;
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
    tip.style.top = (r.top - h - 6 < 4 ? r.bottom + 6 : r.top - h - 6) + 'px';
  }
  document.addEventListener('mouseover', function (e) { var t = e.target.closest('[data-tip]'); if (t) showTip(t); });
  document.addEventListener('mouseout', function (e) { if (e.target.closest('[data-tip]')) tip.hidden = true; });
  document.addEventListener('focusin', function (e) { var t = e.target.closest && e.target.closest('[data-tip]'); if (t) showTip(t); else tip.hidden = true; });
  document.addEventListener('scroll', function () { tip.hidden = true; }, true);

  // ---------- mapa ----------
  var RAMP = { 0: '#46327e', 1: '#365c8d', 2: '#277f8e', 3: '#1fa187', 4: '#a0da39', n: '#b3bcb8' };
  var RAMP_INK = { 0: '#ffffff', 1: '#ffffff', 2: '#ffffff', 3: '#06231a', 4: '#14200a', n: '#1b2521' };
  function feature(l) {
    if (!l._f) {
      var b = bin(l.s);
      l._f = { type: 'Feature', id: l._n, geometry: { type: 'Point', coordinates: [l.lng, l.lat] },
        properties: { id: l.id, label: cop(l.p), n: 1, bin: String(b), sv: l.s == null ? -1 : l.s } };
    }
    return l._f;
  }
  function pinImage(fill, stroke, sw) {
    var r = 2, W = 28, H = 24, rad = 7;
    var c = document.createElement('canvas'); c.width = W * r; c.height = H * r;
    var x = c.getContext('2d'); x.scale(r, r);
    x.beginPath();
    var p = sw / 2 + 0.5;
    x.roundRect ? x.roundRect(p, p, W - 2 * p, H - 2 * p, rad) : x.rect(p, p, W - 2 * p, H - 2 * p);
    x.fillStyle = fill; x.fill();
    x.lineWidth = sw; x.strokeStyle = stroke; x.stroke();
    return { img: { width: W * r, height: H * r, data: x.getImageData(0, 0, W * r, H * r).data },
      opts: { pixelRatio: r, stretchX: [[rad * r, (W - rad) * r]], stretchY: [[rad * r, (H - rad) * r]], content: [6 * r, 4 * r, (W - 6) * r, (H - 4) * r] } };
  }
  function initMap() {
    if (!window.mapboxgl || !window.MAPBOX_TOKEN) return mapFail('No se pudo cargar Mapbox. La lista sigue funcionando.');
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    try {
      map = new mapboxgl.Map({
        container: 'map', style: DARK.matches ? 'mapbox://styles/mapbox/dark-v11' : 'mapbox://styles/mapbox/light-v11',
        center: [-76.522, 3.425], zoom: 11.4, minZoom: 9, maxZoom: 18, attributionControl: true,
        cooperativeGestures: false, locale: { 'NavigationControl.ZoomIn': 'Acercar', 'NavigationControl.ZoomOut': 'Alejar', 'NavigationControl.ResetBearing': 'Restablecer orientación' }
      });
    } catch (e) { return mapFail('Tu navegador no puede mostrar el mapa (WebGL). La lista sigue funcionando.'); }
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-left');
    map.on('error', function (e) { if (e && e.error && /401|403|token/i.test(String(e.error.message || e.error.status))) mapFail('El token de Mapbox no es válido para este dominio.'); });
    map.on('load', function () {
      Object.keys(RAMP).forEach(function (k) {
        var p = pinImage(RAMP[k], DARK.matches ? '#0e1412' : '#ffffff', 1.5);
        map.addImage('pin-' + k, p.img, p.opts);
      });
      var hl = pinImage(DARK.matches ? '#e4ebe8' : '#14201b', DARK.matches ? '#7fc3dd' : '#1d5a74', 3);
      map.addImage('pin-hl', hl.img, hl.opts);

      map.addSource('l', { type: 'geojson', data: fc([]), cluster: true, clusterRadius: 46, clusterMaxZoom: 14,
        clusterProperties: { maxs: ['max', ['get', 'sv']], cnt: ['+', ['get', 'n']] } });
      map.addSource('hl', { type: 'geojson', data: fc([]) });
      var stepColor = function (prop) {
        return ['case', ['<', ['get', prop], 0], RAMP.n, ['step', ['get', prop], RAMP[0], 40, RAMP[1], 55, RAMP[2], 70, RAMP[3], 85, RAMP[4]]];
      };
      map.addLayer({ id: 'clusters', type: 'circle', source: 'l', filter: ['has', 'point_count'], paint: {
        'circle-color': DARK.matches ? '#1d2724' : '#ffffff',
        'circle-radius': ['step', ['get', 'point_count'], 15, 20, 19, 100, 24, 400, 30],
        'circle-stroke-width': 4, 'circle-stroke-color': stepColor('maxs') } });
      map.addLayer({ id: 'cluster-n', type: 'symbol', source: 'l', filter: ['has', 'point_count'], layout: {
        'text-field': ['number-format', ['get', 'cnt'], { locale: 'es-CO' }], 'text-size': 12, 'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'] },
        paint: { 'text-color': DARK.matches ? '#e4ebe8' : '#14201b' } });
      map.addLayer({ id: 'dots', type: 'circle', source: 'l', filter: ['!', ['has', 'point_count']], paint: {
        'circle-radius': 4, 'circle-color': stepColor('sv'), 'circle-stroke-width': 1, 'circle-stroke-color': DARK.matches ? '#0e1412' : '#fff' } });
      map.addLayer({ id: 'pins', type: 'symbol', source: 'l', filter: ['!', ['has', 'point_count']], layout: {
        'icon-image': ['concat', 'pin-', ['get', 'bin']], 'icon-text-fit': 'both', 'icon-text-fit-padding': [2, 6, 2, 6],
        'text-field': ['get', 'label'], 'text-size': 12, 'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'],
        'symbol-sort-key': ['-', 100, ['get', 'sv']],
        'icon-padding': 1, 'text-padding': 1 },
        paint: { 'text-color': ['match', ['get', 'bin'], '3', RAMP_INK[3], '4', RAMP_INK[4], 'n', RAMP_INK.n, '#ffffff'] } });
      map.addLayer({ id: 'hl', type: 'symbol', source: 'hl', layout: {
        'icon-image': 'pin-hl', 'icon-text-fit': 'both', 'icon-text-fit-padding': [3, 8, 3, 8],
        'text-field': ['get', 'label'], 'text-size': 13, 'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'],
        'icon-allow-overlap': true, 'text-allow-overlap': true },
        paint: { 'text-color': DARK.matches ? '#0e1412' : '#ffffff' } });

      ['pins', 'dots'].forEach(function (layer) {
        map.on('mousemove', layer, function (e) { map.getCanvas().style.cursor = 'pointer'; setHot(e.features[0].properties.id, 'map'); });
        map.on('mouseleave', layer, function () { map.getCanvas().style.cursor = ''; setHot(null, 'map'); });
        map.on('click', layer, function (e) {
          var p = e.features[0].properties, l = byId.get(p.id), st = l && stacks.get(skey(l));
          if (st && st.length > 1) showStack(st, e.features[0].geometry.coordinates); else location.hash = encodeURIComponent(p.id);
        });
      });
      map.on('click', 'hl', function (e) { location.hash = encodeURIComponent(e.features[0].properties.id); });
      map.on('mouseenter', 'clusters', function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'clusters', function () { map.getCanvas().style.cursor = ''; });
      map.on('click', 'clusters', function (e) {
        var f = e.features[0];
        map.getSource('l').getClusterExpansionZoom(f.properties.cluster_id, function (err, z) {
          var go = function () { if (!err) map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.2, duration: REDUCED.matches ? 0 : 500 }); };
          if (!err && z >= 15) {  // quizá son ofertas en el mismo punto: no se separan al acercar
            map.getSource('l').getClusterLeaves(f.properties.cluster_id, 40, 0, function (e2, leaves) {
              if (e2 || !leaves.length) return go();
              var l0 = byId.get(leaves[0].properties.id), k0 = l0 && skey(l0);
              var same = leaves.every(function (x) { var q = byId.get(x.properties.id); return q && skey(q) === k0; });
              var st = same && stacks.get(k0);
              if (st && st.length > 1) showStack(st, f.geometry.coordinates); else go();
            });
          } else go();
        });
      });
      map.on('moveend', function () { if (quietMove) { quietMove = false; return; } if (state.area) updateVisible(); });
      mapReady = true;
      updateMapData();
      updateVisible(true);
      if (pendingFit) { pendingFit = false; fitTo(filtered); }
      initLayers();
      if (current) setHl(current.id);
    });
  }

  // ---------- capas opcionales (RF6) ----------
  var LAYERS = [
    { k: 'heat', label: 'Calor de $/m²', note: 'Densidad de precio/m² de las ofertas' },
    { k: 'flood', label: 'Riesgo de inundación', files: ['inundacion_fluvial', 'inundacion_pluvial', 'inundacion_no_mitigable'] },
    { k: 'reno', label: 'Renovación urbana', files: ['tratamientos_renovacion'] },
    { k: 'tron', label: 'Troncales y tren proyectados', files: ['troncales_proyectadas', 'corredor_ferreo'] }
  ];
  var layerOn = {}, layerLoaded = {};
  function initLayers() {
    var host = $('#layersCtl'); if (!host || host.dataset.ready) return;
    host.dataset.ready = '1';
    host.hidden = false;
    var body = $('#layersBody');
    body.innerHTML = LAYERS.map(function (L) {
      return '<label class="layer-opt"><input type="checkbox" data-layer="' + L.k + '"><span>' + esc(L.label) + '</span></label>';
    }).join('') + '<p class="layer-note" id="layerNote" role="status"></p>';
    $('#layersBtn').addEventListener('click', function () {
      var open = body.hidden; body.hidden = !open; this.setAttribute('aria-expanded', open);
    });
    body.addEventListener('change', function (e) {
      var k = e.target.getAttribute('data-layer'); if (!k) return;
      layerOn[k] = e.target.checked;
      setLayer(k);
    });
  }
  function loadGJ(name) {
    return fetch('data/layers/' + name + '.geojson').then(function (r) { if (!r.ok) throw new Error(name + ' HTTP ' + r.status); return r.json(); });
  }
  function ensureLayer(k) {
    if (layerLoaded[k]) return layerLoaded[k];
    var below = 'clusters';  // debajo de los pines de ofertas
    var D = DARK.matches;
    var addAll = function (defs) { defs.forEach(function (d) { if (!map.getSource(d.id)) map.addSource(d.id, { type: 'geojson', data: d.data }); d.layers.forEach(function (ly) { ly.source = d.id; map.addLayer(ly, below); }); }); };
    if (k === 'heat') {
      layerLoaded[k] = Promise.resolve().then(function () {
        var feats = all.filter(function (l) { return l.pm && l.pm < 3e7 && l._geo; }).map(function (l) {
          return { type: 'Feature', geometry: { type: 'Point', coordinates: [l.lng, l.lat] }, properties: { pm: l.pm / 1e6 } };
        });
        addAll([{ id: 'heat-src', data: fc(feats), layers: [{ id: 'heat', type: 'heatmap', maxzoom: 16, paint: {
          'heatmap-weight': ['interpolate', ['linear'], ['get', 'pm'], 2.5, 0.1, 9, 1],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.6, 15, 2],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 12, 15, 40],
          'heatmap-opacity': 0.7,
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(255,255,204,0)', 0.2, '#a1dab4', 0.45, '#41b6c4', 0.7, '#2c7fb8', 1, '#253494'] } }] }]);
      });
    } else if (k === 'flood') {
      layerLoaded[k] = Promise.all(LAYERS[1].files.map(loadGJ)).then(function (g) {
        var names = LAYERS[1].files;
        addAll(names.map(function (n, i) {
          return { id: n, data: g[i], layers: [
            { id: n + '-f', type: 'fill', paint: { 'fill-color': n === 'inundacion_pluvial' ? '#4aa3df' : '#1b6fd0', 'fill-opacity': n === 'inundacion_no_mitigable' ? 0.55 : 0.32 } },
            { id: n + '-l', type: 'line', paint: { 'line-color': '#1b6fd0', 'line-width': 0.6, 'line-opacity': 0.7 } }] };
        }));
      });
    } else if (k === 'reno') {
      layerLoaded[k] = loadGJ('tratamientos_renovacion').then(function (g) {
        addAll([{ id: 'reno', data: g, layers: [
          { id: 'reno-f', type: 'fill', paint: { 'fill-color': '#d97706', 'fill-opacity': 0.3 } },
          { id: 'reno-l', type: 'line', paint: { 'line-color': '#b45309', 'line-width': 1.4, 'line-dasharray': [2, 1] } }] }]);
      });
    } else {
      layerLoaded[k] = Promise.all(LAYERS[3].files.map(loadGJ)).then(function (g) {
        addAll([{ id: 'tron', data: g[0], layers: [{ id: 'tron-l', type: 'line', layout: { 'line-cap': 'round' }, paint: { 'line-color': D ? '#ff9db0' : '#c2185b', 'line-width': 3.5, 'line-dasharray': [1.5, 1.2] } }] },
          { id: 'tren', data: g[1], layers: [{ id: 'tren-l', type: 'line', layout: { 'line-cap': 'round' }, paint: { 'line-color': D ? '#ffd166' : '#7a4a00', 'line-width': 3, 'line-dasharray': [4, 2] } }] }]);
      });
    }
    return layerLoaded[k];
  }
  function layerIds(k) {
    return { heat: ['heat'], flood: LAYERS[1].files.reduce(function (a, n) { return a.concat([n + '-f', n + '-l']); }, []), reno: ['reno-f', 'reno-l'], tron: ['tron-l', 'tren-l'] }[k];
  }
  function setLayer(k) {
    if (!mapReady) return;
    var note = $('#layerNote'); note.textContent = '';
    ensureLayer(k).then(function () {
      layerIds(k).forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', layerOn[k] ? 'visible' : 'none'); });
    }).catch(function () {
      layerLoaded[k] = null; layerOn[k] = false;
      var cb = document.querySelector('[data-layer="' + k + '"]'); if (cb) cb.checked = false;
      note.textContent = 'No se pudo cargar la capa.';
    });
  }
  function mapFail(msg) {
    mapReady = false;
    var w = $('#mapWrap');
    if (!w.querySelector('.map-fallback')) { var d = document.createElement('div'); d.className = 'map-fallback'; d.textContent = msg; w.appendChild(d); }
    $('#areaToggle').disabled = true;
    updateVisible();
  }
  function fc(feats) { return { type: 'FeatureCollection', features: feats }; }
  // Ofertas apiladas en una misma coordenada (ubicación aproximada = centro del barrio): se cuentan y se listan en un popup.
  var stacks = new Map();
  function skey(l) { return l.lat + ',' + l.lng; }
  function updateMapData() {
    if (!mapReady) return;
    stacks = new Map();
    var feats = [];
    for (var i = 0; i < filtered.length; i++) {
      var l = filtered[i]; if (!l._geo) continue;
      var k = skey(l), a = stacks.get(k); if (a) a.push(l); else stacks.set(k, [l]);
    }
    for (var j = 0; j < filtered.length; j++) {
      var m = filtered[j]; if (!m._geo) continue;
      var f = feature(m), n = stacks.get(skey(m)).length;
      var lab = n > 1 ? (stacks.get(skey(m))[0] === m ? cop(m.p) + ' +' + (n - 1) : null) : cop(m.p);
      if (lab === null) continue;  // en una pila solo se dibuja la mejor oferta
      f.properties.label = lab; f.properties.n = n;
      feats.push(f);
    }
    map.getSource('l').setData(fc(feats));
  }
  var stackPopup = null;
  function showStack(list, lngLat) {
    if (stackPopup) stackPopup.remove();
    var top = list.slice(0, 40);
    var html = '<div class="stack-pop"><b>' + NF.format(list.length) + ' ofertas en este punto</b><small>' + (list[0].gp === 'b' ? 'Ubicación aproximada: centro del barrio ' + esc(list[0].br) : 'Misma ubicación exacta') + '</small><ul>' +
      top.map(function (x) {
        return '<li><a href="#' + encodeURIComponent(x.id) + '"><span class="num">' + cop(x.p) + '</span><span>' + (x.a ? NF.format(x.a) + ' m²' : 'sin área') + (x.b != null ? ' · ' + x.b + ' hab' : '') + '</span>' + scoreBadge(x.s) + '</a></li>';
      }).join('') + '</ul>' + (list.length > top.length ? '<small>y ' + NF.format(list.length - top.length) + ' más: usa los filtros para acotar.</small>' : '') + '</div>';
    stackPopup = new mapboxgl.Popup({ maxWidth: '300px', closeButton: true }).setLngLat(lngLat).setHTML(html).addTo(map);
  }
  function setHl(id) {
    if (!mapReady) return;
    var l = id && byId.get(id);
    map.getSource('hl').setData(fc(l ? [feature(l)] : []));
  }
  function setHot(id, from) {
    if (id === hotId) return;
    var prev = hotId && listEl.querySelector('.card.hot'); if (prev) prev.classList.remove('hot');
    hotId = id;
    if (id) { var c = listEl.querySelector('.card[data-id="' + CSS.escape(id) + '"]'); if (c) c.classList.add('hot'); }
    setHl(id || (current && current.id));
  }
  // Lleva la oferta a la vista si el cajón de detalle la tapa (sin recargar la lista).
  var quietMove = false;
  function revealOnMap(l) {
    if (!mapReady || !l._geo || MOBILE.matches) return;
    var c = map.getContainer(), dw = detailEl.offsetWidth, pt = map.project([l.lng, l.lat]), m = 40;
    if (pt.x > m && pt.x < c.clientWidth - dw - m && pt.y > m && pt.y < c.clientHeight - m) return;
    quietMove = true;
    map.easeTo({ center: [l.lng, l.lat], offset: [-dw / 2, 0], zoom: Math.max(map.getZoom(), 13), duration: REDUCED.matches ? 0 : 700 });
  }
  function fitTo(list) {
    if (!mapReady || !list.length) return;
    var b = new mapboxgl.LngLatBounds();
    list.forEach(function (l) { if (l._geo) b.extend([l.lng, l.lat]); });
    if (b.isEmpty()) return;
    map.fitBounds(b, { padding: 40, maxZoom: 15, duration: REDUCED.matches ? 0 : 600 });
  }

  // ---------- detalle ----------
  var shardCache = new Map();
  function loadDetail(id) {
    var key = md5(id).slice(0, 2);
    if (!shardCache.has(key)) {
      shardCache.set(key, fetch('data/detail/' + key + '.json').then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status); return r.json();
      }).catch(function (e) { shardCache.delete(key); throw e; }));
    }
    return shardCache.get(key).then(function (sh) { return sh[id] || null; });
  }
  var detailEl = $('#detail'), detailBody = $('#detailBody'), lastFocus = null;
  function openDetail(id) {
    var l = byId.get(id);
    if (!lastFocus || !detailEl.contains(document.activeElement)) lastFocus = document.activeElement;
    current = l || { id: id };
    document.body.classList.add('detail-open');
    detailEl.inert = false; detailEl.classList.add('open');
    listEl.querySelectorAll('.card.current').forEach(function (c) { c.classList.remove('current'); });
    var cc = listEl.querySelector('.card[data-id="' + CSS.escape(id) + '"]'); if (cc) cc.classList.add('current');
    $('#detailBarTitle').textContent = l ? (l.br || '') : '';
    var fb = $('#detailFav'); fb.hidden = !l; fb.setAttribute('data-id', id); setFavBtn(fb, id);
    destroyMini();
    if (!l) {
      detailBody.innerHTML = '<div class="empty"><h2>Esta oferta ya no está activa</h2><p>Puede que se haya vendido o retirado del portal desde la última actualización.</p><button type="button" class="btn btn-primary" data-close>Volver a la lista</button></div>';
    } else {
      detailBody.innerHTML = headHTML(l, null) + '<p class="loading">Cargando detalle…</p>';
      setHl(id);
      revealOnMap(l);
      loadDetail(id).then(function (d) {
        if (!current || current.id !== id) return;
        detailBody.innerHTML = headHTML(l, d) + (d ? bodyHTML(l, d) : '<p class="loading">No hay detalle para esta oferta.</p>');
        if (d) initMini(l, d);
      }).catch(function () {
        if (!current || current.id !== id) return;
        detailBody.querySelector('.loading').innerHTML = 'No se pudo cargar el detalle. <button type="button" class="btn" data-retry>Reintentar</button>';
      });
    }
    detailBody.scrollTop = 0;
    detailEl.focus({ preventScroll: true });
  }
  function closeDetail() {
    if (!current) return;
    var id = current.id;
    current = null;
    detailEl.classList.remove('open'); detailEl.inert = true;
    document.body.classList.remove('detail-open');
    destroyMini();
    setHl(hotId);
    listEl.querySelectorAll('.card.current').forEach(function (c) { c.classList.remove('current'); });
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    var card = listEl.querySelector('.card[data-id="' + CSS.escape(id) + '"] .card-link');
    if (card) card.focus({ preventScroll: false }); else if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    lastFocus = null;
  }
  $('#detailClose').addEventListener('click', closeDetail);
  $('#detailFav').addEventListener('click', function (e) { toggleFav(e.currentTarget.getAttribute('data-id')); });
  detailBody.addEventListener('click', function (e) {
    if (e.target.closest('[data-close]')) closeDetail();
    if (e.target.closest('[data-retry]') && current) openDetail(current.id);
  });
  var pendingFit = false;
  function routeHash() {
    var id = decodeURIComponent(location.hash.slice(1));
    var cmm = /^comuna=(\d+)$/.exec(id);
    if (cmm) { state.f.zona = { cm: [+cmm[1]], br: [] }; history.replaceState(null, '', location.pathname + location.search); apply(); if (mapReady) fitTo(filtered); else pendingFit = true; return; }
    var bm = /^barrio=(.+)$/.exec(id);
    if (bm) {  // enlace desde Zonas: #barrio=<código> preconfigura el filtro de zona
      state.f.zona = { cm: [], br: [bm[1]] };
      history.replaceState(null, '', location.pathname + location.search);
      apply();
      if (mapReady) fitTo(filtered); else pendingFit = true;
      return;
    }
    if (id) openDetail(id); else if (current) closeDetail();
  }
  window.addEventListener('hashchange', routeHash);

  function headHTML(l, d) {
    var photos = d ? d.photos : (l.img ? [l.img] : []);
    var gallery = photos.length
      ? '<div class="gallery" tabindex="0" aria-label="Fotos (' + photos.length + ')">' + photos.map(function (u, i) {
        return '<img src="' + esc(u) + '" alt="Foto ' + (i + 1) + ' de ' + photos.length + '" loading="' + (i < 2 ? 'eager' : 'lazy') + '" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">';
      }).join('') + '</div><p class="gallery-count">' + photos.length + ' ' + (photos.length === 1 ? 'foto' : 'fotos') + (photos.length > 1 ? ' · desliza para ver más' : '') + '</p>'
      : '<div class="gallery"><div class="no-photo">El anuncio no tiene fotos</div></div>';
    var title = d ? d.title : (l.t === 'c' ? 'Casa' : 'Apartamento') + ' en venta';
    var sub = ['<span class="br">' + esc(l.br || 'Barrio sin dato') + '</span>', l.cm ? 'Comuna ' + esc(l.cm) : 'Fuera de las comunas', l.e ? 'Estrato ' + esc(l.e) : null,
      l.a ? NF.format(l.a) + ' m²' : 'sin área', l.b != null ? l.b + ' hab' : null, l.ba != null ? l.ba + (l.ba === 1 ? ' baño' : ' baños') : null,
      (l.pk || 0) + ' parq', l.age != null ? l.age + ' años' : null, d && d.floor ? 'piso ' + d.floor : null,
      d && d.admin ? 'admin ' + copFull(d.admin) : null].filter(Boolean).map(function (x) { return '<span>' + x + '</span>'; }).join('');
    var tags = visTags(l).map(function (t) { return '<span class="tag tag-' + t + '">' + esc(t === 'rebaja' && l.pd != null ? 'Rebaja ' + pct(l.pd) : TAGS[t] || t) + '</span>'; }).join('');
    var links = d && d.urls && d.urls.length ? '<div class="d-links">' + d.urls.map(function (u) {
      var name = /fincaraiz/.test(u) ? 'Finca Raíz' : /metrocuadrado/.test(u) ? 'Metrocuadrado' : (u.split('/')[2] || 'portal');
      return '<a class="btn" href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">Ver en ' + esc(name) + ' ↗</a>';
    }).join('') + '</div>' : '';
    return gallery + '<div class="d-head"><h1 id="dTitle">' + esc(title) + '</h1>' +
      '<div class="d-price-row"><span class="d-price num">' + cop(l.p) + '</span>' + scoreBadge(l.s) + '</div>' +
      '<div class="d-sub"><span class="num">' + (l.pm ? copM1(l.pm) + '/m²' : 'sin $/m²') + '</span>' +
      (l.d != null ? '<span class="dchip' + (l.d > 0 ? '' : ' flat') + '">' + pct(-l.d, true) + ' vs zona</span>' : '') +
      (l.up != null && l.up > 0 ? '<span class="dchip">upside ' + upTxt(l.up) + '</span>' : '') + '</div>' +
      '<div class="d-sub">' + sub + '</div>' +
      (tags ? '<div class="badges" style="margin-top:8px">' + tags + '</div>' : '') + links + '</div>';
  }

  function bodyHTML(l, d) {
    var out = '';
    // Por qué está aquí
    var c = l.c || [0, 0, 0, 0, 0];
    var amenTop = (d.amen || []).slice().sort(function (a, b) { return a.dist - b.dist; }).slice(0, 2)
      .map(function (a) { return a.name + ' a ' + meters(a.dist); }).join(' · ');
    var z = d.zone || {};
    var proj = (d.projects || [])[0];
    var texts = [
      l.d != null ? (l.d > 0 ? pct(l.d) + ' por debajo de comparables' : 'Precio ' + pct(-l.d, true) + ' frente a comparables') + '. ' + (d.comps_info || '') : 'Sin descuento calculable (faltan datos).',
      l.up != null ? 'Podría valer ' + pct(l.up, true) + ' por m² si queda como los mejores de la zona (ARV ' + copM1(d.arv_m2) + '/m²).' : 'Sin potencial de reventa calculable.',
      (z.trend_12m != null ? 'Zona ' + (z.trend_12m >= 0 ? 'en valorización: ' : 'a la baja: ') + pct(z.trend_12m, true) + ' en 12 meses' : 'Tendencia de zona sin datos aún') + (proj ? '; ' + proj.name + ' a ' + meters(proj.dist) : '') + '.',
      amenTop ? 'Cerca de ' + amenTop + '.' : 'Pocas amenidades registradas cerca.',
      z.dom != null ? 'En la zona se vende en unos ' + z.dom + ' días (mediana).' : 'Liquidez sin datos aún.'
    ];
    var rs = l.s == null ? [] : COMP.map(function (k, i) { return { i: i, pts: k.w * c[i], t: texts[i] }; }).sort(function (a, b) { return b.pts - a.pts; }).slice(0, 3);
    out += '<section class="sec"><h2>Por qué está aquí</h2><ul class="reasons">' + rs.map(function (r) {
      return '<li><span class="pts plus">+' + NF1.format(r.pts) + '</span><span><b>' + COMP[r.i].k + '.</b> ' + esc(r.t) + '</span></li>';
    }).join('') + l.pen.map(function (p) {
      return '<li><span class="pts ' + (p.pts ? 'minus' : 'zero') + '">' + (p.pts ? '−' + p.pts : '0') + '</span><span>' + esc(p.why) + (p.pts ? '' : ' (baja la confianza)') + '</span></li>';
    }).join('') + '</ul>' + (l.s == null ? '<p class="sec-note">No es oportunidad: para ranquear se necesita precio, área &gt; 20 m², estrato ≥ 2 y descuento positivo' + (l.tags.indexOf('sospechoso') >= 0 ? '; el precio parece irreal (posible arriendo publicado como venta)' : '') + '.</p>' : '') + '</section>';

    // Desglose
    var base = COMP.reduce(function (s, k, i) { return s + k.w * c[i]; }, 0);
    var penSum = l.pen.reduce(function (s, p) { return s + p.pts; }, 0);
    out += '<section class="sec"><h2>Desglose del puntaje</h2><div class="bars">' + COMP.map(function (k, i) {
      return '<div class="bar-row"><span>C' + (i + 1) + ' ' + k.k + '<small>' + Math.round(k.w * 100) + '% · ' + k.note + '</small></span>' +
        '<span class="bar" role="img" aria-label="' + k.k + ': ' + c[i] + ' de 100"><i style="width:' + Math.max(0, Math.min(100, c[i])) + '%"></i></span>' +
        '<span class="bar-val">' + c[i] + '</span></div>';
    }).join('') + '</div><div class="formula">Base ponderada <b>' + NF1.format(base) + '</b> − penalizaciones <b>' + penSum + '</b>' +
      ' × confianza (0,6 + 0,4 × ' + NF1.format(l.conf == null ? 0 : l.conf) + ') = <b>' + (l.s == null ? 'no ranqueable' : NF1.format(l.s)) + '</b>' +
      '<br>C1 y C2 son percentiles dentro de Cali. La confianza baja con pocos comparables, ubicación aproximada o sin área.</div></section>';

    // Potencial de reventa
    if (l.a && d.arv_m2) {
      var arv = d.arv_m2 * l.a, upCop = (d.arv_m2 - l.pm) * l.a;
      var mo = d.max_offer_70, gap = mo ? l.p / mo - 1 : null;
      out += '<section class="sec"><h2>Potencial de reventa</h2><dl class="kv">' +
        '<div><dt>Valor después de remodelar (ARV)</dt><dd>' + cop(arv) + ' <small>' + copM1(d.arv_m2) + '/m²</small></dd></div>' +
        '<div><dt>Upside</dt><dd class="' + (upCop > 0 ? 'd-good' : '') + '">' + (upCop >= 0 ? '+' : '−') + cop(Math.abs(upCop)) + ' <small>' + pct(l.up, true) + '</small></dd></div>' +
        '<div><dt>Valor esperado hoy</dt><dd>' + cop(d.expected_m2 * l.a) + ' <small>' + copM1(d.expected_m2) + '/m²</small></dd></div>' +
        '<div><dt>Compra máx. regla del 70%</dt><dd>' + cop(mo) + ' <small>ARV × 0,70</small></dd></div></dl>' +
        (gap != null ? '<p class="verdict">El precio pedido (' + cop(l.p) + ') está <b class="' + (gap <= 0 ? 'd-good' : '') + '">' + (Math.abs(gap) < 0.005 ? 'prácticamente igual a' : gap < 0 ? pct(-gap) + ' por debajo' : pct(gap) + ' por encima') + '</b> de la referencia del 70%. Sin costos de remodelación: es solo una referencia.</p>' : '') +
        '</section>';
    }

    // Comparables
    var comps = d.comps || [];
    out += '<section class="sec"><h2>Comparables</h2><p class="sec-note">' + esc(d.comps_info || '') + '</p>' +
      (comps.length ? '<div class="minimap" id="minimap" role="img" aria-label="Mapa de la oferta y sus comparables"></div>' +
        '<table><thead><tr><th>Comparable</th><th class="r">$/m²</th><th class="r">Área</th><th class="r">Distancia</th></tr></thead><tbody>' +
        comps.map(function (k) {
          var diff = l.pm ? k.pm / l.pm - 1 : null;
          return '<tr><td><a href="' + esc(k.url) + '" target="_blank" rel="noopener noreferrer">' + esc(k.id) + '</a></td><td class="r">' + copM1(k.pm) +
            (diff != null ? ' <small class="' + (diff > 0 ? 'd-good' : 'd-flat') + '">' + pct(diff, true) + '</small>' : '') + '</td><td class="r">' + NF.format(k.a) + ' m²</td><td class="r">' + meters(k.dist) + '</td></tr>';
        }).join('') + '</tbody></table>' : '<p class="sec-note">Sin comparables suficientes.</p>') + '</section>';

    // Historial
    var h = d.history || [];
    out += '<section class="sec"><h2>Historial de precios</h2>' + (h.length ? '<table><thead><tr><th>Fecha</th><th class="r">Precio</th><th class="r">Cambio</th></tr></thead><tbody>' +
      h.map(function (x, i) {
        var ch = i ? x.p / h[i - 1].p - 1 : null;
        return '<tr><td>' + fmtDate(x.d) + (i === 0 ? ' <small class="d-flat">publicada</small>' : '') + '</td><td class="r">' + copFull(x.p) + '</td><td class="r ' + (ch != null && ch < 0 ? 'd-good' : '') + '">' + (ch == null ? '—' : pct(ch, true)) + '</td></tr>';
      }).join('') + '</tbody></table>' : '<p class="sec-note">Sin cambios de precio registrados.</p>') +
      '<p class="sec-note">Vista por primera vez el ' + fmtDate(l.fs) + ' · ' + l.dom + ' días en el mercado.</p></section>';

    // Qué hay cerca
    var groups = {};
    (d.amen || []).forEach(function (a) { (groups[a.cat] = groups[a.cat] || []).push(a); });
    var cats = Object.keys(AMEN).filter(function (k) { return groups[k]; }).concat(Object.keys(groups).filter(function (k) { return !AMEN[k]; }));
    out += '<section class="sec"><h2>Qué hay cerca</h2>' + (cats.length ? '<div class="amen">' + cats.map(function (k) {
      return '<div class="amen-cat"><h3>' + esc(AMEN[k] || k) + '</h3><ul>' + groups[k].sort(function (a, b) { return a.dist - b.dist; }).map(function (a) {
        return '<li><span>' + esc(a.name) + '</span><span class="dist">' + meters(a.dist) + ' · ' + walkMin(a.dist) + ' min a pie</span></li>';
      }).join('') + '</ul></div>';
    }).join('') + '</div><p class="sec-note">Distancia en línea recta; los minutos a pie la multiplican por 1,3.</p>' : '<p class="sec-note">Sin amenidades registradas cerca.</p>') + '</section>';

    // Riesgos y proyectos
    var risks = d.risks || [], projects = d.projects || [];
    out += '<section class="sec"><h2>Riesgos y proyectos</h2>' +
      (risks.length ? '<ul class="plain-list">' + risks.map(function (r) { return '<li><span class="pen">' + esc(PEN_SHORT[r.k] || r.k) + '</span> ' + esc(r.why) + '</li>'; }).join('') + '</ul>' : '<p class="sec-note">Sin riesgos registrados en las capas oficiales.</p>') +
      (projects.length ? '<ul class="plain-list" style="margin-top:10px">' + projects.map(function (p) { return '<li><span class="tag tag-nueva">' + esc(PROJ[p.k] || p.k) + '</span> ' + esc(p.name) + ' a ' + meters(p.dist) + '</li>'; }).join('') + '</ul>' : '<p class="sec-note">Sin proyectos urbanos cerca.</p>') +
      (d.zone ? '<dl class="kv" style="margin-top:12px"><div><dt>Mediana $/m² del barrio</dt><dd>' + copM1(d.zone.median_m2) + '</dd></div>' +
        '<div><dt>Tendencia 12 meses</dt><dd>' + (d.zone.trend_12m == null ? 'sin datos aún' : pct(d.zone.trend_12m, true)) + '</dd></div>' +
        '<div><dt>Días en mercado (mediana)</dt><dd>' + (d.zone.dom == null ? '—' : d.zone.dom) + '</dd></div>' +
        '<div><dt>Rentabilidad por arriendo</dt><dd>' + (d.zone.yield == null ? '—' : NF1.format(d.zone.yield * 100) + '%') + '</dd></div></dl>' : '') +
      '</section>';

    out += '<section class="sec"><h2>Descripción</h2><p class="desc">' + esc(d.desc || 'Sin descripción.') + '</p>' +
      (d.area_lot ? '<p class="sec-note">Área de lote: ' + NF.format(d.area_lot) + ' m²</p>' : '') + '</section>';
    if (d.urls && d.urls.length) {
      out += '<section class="sec"><h2>Anuncios</h2><ul class="plain-list">' + d.urls.map(function (u) {
        return '<li><a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer" style="overflow-wrap:anywhere">' + esc(u) + '</a></li>';
      }).join('') + '</ul></section>';
    }
    return out;
  }

  function initMini(l, d) {
    var el = $('#minimap');
    if (!el || !window.mapboxgl || !mapReady) { if (el) el.remove(); return; }
    var comps = d.comps || [];
    try {
      miniMap = new mapboxgl.Map({ container: el, style: DARK.matches ? 'mapbox://styles/mapbox/dark-v11' : 'mapbox://styles/mapbox/light-v11',
        center: [l.lng, l.lat], zoom: 14, attributionControl: false, cooperativeGestures: true });
    } catch (e) { el.remove(); return; }
    miniMap.on('load', function () {
      miniMap.addSource('c', { type: 'geojson', data: fc(comps.map(function (k) {
        return { type: 'Feature', geometry: { type: 'Point', coordinates: [k.lng, k.lat] }, properties: { label: copM1(k.pm) } };
      })) });
      miniMap.addSource('s', { type: 'geojson', data: fc([{ type: 'Feature', geometry: { type: 'Point', coordinates: [l.lng, l.lat] }, properties: { label: copM1(l.pm) + ' esta' } }]) });
      miniMap.addLayer({ id: 'c', type: 'circle', source: 'c', paint: { 'circle-radius': 5, 'circle-color': '#277f8e', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } });
      miniMap.addLayer({ id: 'cl', type: 'symbol', source: 'c', layout: { 'text-field': ['get', 'label'], 'text-size': 10, 'text-offset': [0, 1.1], 'text-font': ['DIN Pro Medium', 'Arial Unicode MS Regular'] },
        paint: { 'text-color': DARK.matches ? '#e4ebe8' : '#14201b', 'text-halo-color': DARK.matches ? '#0e1412' : '#fff', 'text-halo-width': 1.2 } });
      miniMap.addLayer({ id: 's', type: 'circle', source: 's', paint: { 'circle-radius': 8, 'circle-color': DARK.matches ? '#e4ebe8' : '#14201b', 'circle-stroke-width': 3, 'circle-stroke-color': DARK.matches ? '#7fc3dd' : '#1d5a74' } });
      var b = new mapboxgl.LngLatBounds([l.lng, l.lat], [l.lng, l.lat]);
      comps.forEach(function (k) { b.extend([k.lng, k.lat]); });
      miniMap.resize();
      miniMap.fitBounds(b, { padding: 28, duration: 0, maxZoom: 16 });
    });
  }
  function destroyMini() { if (miniMap) { try { miniMap.remove(); } catch (e) { /* ya removido */ } miniMap = null; } }

  // ---------- hoja inferior móvil ----------
  var sheet = $('#sheet'), sheetY = null, snaps = null;
  function layoutSheet() {
    if (!MOBILE.matches) { sheet.style.transform = ''; return; }
    var H = $('#explore').clientHeight;
    snaps = { full: 0, half: Math.round(H * 0.45), peek: H - 96 };
    setSheet(sheetY == null ? snaps.half : nearestSnap(sheetY), false);
  }
  function nearestSnap(y) {
    var best = 'half', bd = Infinity;
    Object.keys(snaps).forEach(function (k) { var dd = Math.abs(snaps[k] - y); if (dd < bd) { bd = dd; best = k; } });
    return snaps[best];
  }
  function setSheet(y, animate) {
    sheetY = y;
    sheet.classList.toggle('anim', !!animate);
    sheet.style.transform = 'translateY(' + y + 'px)';
    var vt = $('#viewToggle');
    var isFull = snaps && y <= snaps.full + 4;
    vt.textContent = isFull ? 'Mapa' : 'Lista';
    vt.setAttribute('aria-label', isFull ? 'Ver el mapa' : 'Ver la lista');
  }
  (function dragSheet() {
    var startY = 0, startSheet = 0, lastY = 0, lastT = 0, vel = 0, dragging = false;
    function down(e) {
      if (!MOBILE.matches) return;
      if (e.target.closest('select, button, a, input')) return;
      dragging = true; startY = lastY = e.clientY; lastT = e.timeStamp; startSheet = sheetY; vel = 0;
      sheet.classList.remove('anim');
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    function move(e) {
      if (!dragging) return;
      var y = Math.max(snaps.full, Math.min(snaps.peek + 40, startSheet + e.clientY - startY));
      var dt = e.timeStamp - lastT; if (dt > 0) vel = (e.clientY - lastY) / dt;
      lastY = e.clientY; lastT = e.timeStamp;
      sheet.style.transform = 'translateY(' + y + 'px)'; sheetY = y;
    }
    function up() {
      if (!dragging) return; dragging = false;
      var target = sheetY + vel * 180; // proyección por velocidad
      setSheet(nearestSnap(target), !REDUCED.matches);
    }
    [$('#sheetHandle'), $('.list-head')].forEach(function (h) {
      h.addEventListener('pointerdown', down);
      h.addEventListener('pointermove', move);
      h.addEventListener('pointerup', up);
      h.addEventListener('pointercancel', up);
    });
  })();
  $('#viewToggle').addEventListener('click', function () {
    if (!snaps) return;
    setSheet(sheetY <= snaps.full + 4 ? snaps.peek : snaps.full, !REDUCED.matches);
  });
  MOBILE.addEventListener('change', function () { sheetY = null; layoutSheet(); closePanel(); if (map) setTimeout(function () { map.resize(); }, 50); });

  // ---------- arranque ----------
  function prep(l, i) {
    l._n = i;
    var t = {}; l.tags = l.tags || []; l.pen = l.pen || [];
    l.tags.forEach(function (x) { t[x] = true; });
    l._tags = t; l._npen = nPen(l);
    if (l.br == null) l.br = '';
    // Sanear datos sucios de los portales.
    if (l.cm == null) l.cm = 0;
    if (l.b != null && l.b < 0) l.b = null;
    if (l.ba != null && l.ba < 0) l.ba = null;
    if (l.pk != null && l.pk < 0) l.pk = 0;
    if (l.a != null && l.a > 5000) l.a = null;      // áreas absurdas (lotes mal digitados)
    if (l.s == null && !l.conf) { l.d = null; l.up = null; }  // sin cálculo: no mostrar 0 % engañoso
    l._geo = l.lat > 2.5 && l.lat < 4.8 && l.lng > -77.5 && l.lng < -75.5;
    return l;
  }
  function boot() {
    layoutSheet();
    renderChips();
    Promise.all([
      fetch('data/listings.json').then(function (r) { if (!r.ok) throw new Error('listings.json HTTP ' + r.status); return r.json(); }),
      fetch('data/zones.json').then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; }),
      fetch('data/meta.json').then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })
    ]).then(function (res) {
      all = (res[0].listings || []).filter(function (l) { return l && l.id; }).map(prep);
      all.forEach(function (l) { byId.set(l.id, l); });
      zones = res[1] || {}; meta = res[2] || {};
      var gen = meta.generated_at || res[0].generated_at;
      if (gen) { $('#updated').textContent = 'Actualizado ' + DTF.format(new Date(gen)); today = new Date(gen); }
      $('#fixture').hidden = !meta.fixture;
      var bad = window.SIC && SIC.runsWithErrors(meta);
      if (bad && bad.length) { var w = $('#runWarn'); w.hidden = false; w.title = 'Última corrida con errores: ' + bad.map(function (r) { return r.source; }).join(', '); }
      // comunas → barrios con conteo, desde las ofertas (zones.json aporta nombres si faltan).
      var cm = {};
      all.forEach(function (l) {
        var c = cm[l.cm] = cm[l.cm] || { cm: l.cm, n: 0, br: {} };
        c.n++;
        if (!l.cm) return;
        var b = c.br[l.brc] = c.br[l.brc] || { code: l.brc, name: l.br || (zones[l.brc] && zones[l.brc].name) || l.brc, n: 0 };
        b.n++;
      });
      comunas = Object.keys(cm).map(function (k) {
        var c = cm[k]; c.barrios = Object.keys(c.br).map(function (x) { return c.br[x]; }).sort(function (a, b) { return a.name.localeCompare(b.name, 'es'); });
        return c;
      }).sort(function (a, b) { return (a.cm || 99) - (b.cm || 99); });
      nuevaShare = all.filter(function (l) { return l._tags.nueva; }).length / (all.length || 1);
      apply();
      initMap();
      if (location.hash.length > 1) routeHash();
    }).catch(function (e) {
      listEl.innerHTML = '';
      $('#count').textContent = 'No se pudieron cargar las ofertas.';
      $('#empty').hidden = false;
      $('#empty').innerHTML = '<h2>No se pudieron cargar las ofertas</h2><p>' + esc(e.message) + '. Revisa que exista web/data/listings.json (corre el export o <code>python3 web/dev/make_fixture.py</code>).</p><button type="button" class="btn btn-primary" onclick="location.reload()">Reintentar</button>';
      initMap();
    });
  }
  boot();
})();

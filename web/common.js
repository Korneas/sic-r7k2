/* Utilidades compartidas entre páginas (sin build). */
window.SIC = (function () {
  function nErr(r) { return Array.isArray(r.errors) ? r.errors.length : (r.errors || 0); }
  // Última corrida por fuente que tuvo errores (o ok === false).
  function runsWithErrors(meta) {
    var latest = {};
    (meta && meta.runs || []).forEach(function (r) {
      var k = r.source || '*';
      if (!latest[k] || String(r.at) > String(latest[k].at)) latest[k] = r;
    });
    return Object.keys(latest).map(function (k) { return latest[k]; }).filter(function (r) { return nErr(r) > 0 || r.ok === false; })
      .map(function (r) { return { source: r.source || 'todas', at: r.at, errors: nErr(r) }; });
  }
  return { runsWithErrors: runsWithErrors, nErr: nErr };
})();

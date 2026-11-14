"""Corrida: extrae de cada fuente, filtra, hace upsert en SQLite y corre el pipeline.

Uso (desde la raíz del repo):
  .venv/bin/python -m scraper.main --mode full|incremental [--sources fincaraiz,metrocuadrado] [--max-pages N]
Dev: SCRAPER_RAW=archivo.json reutiliza la extracción guardada (si no existe, la guarda).
"""
import argparse
import importlib
import importlib.util
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import yaml

from scraper import db
from scraper.net import Http

PIPELINE = [("dedupe", False), ("geo", False), ("score", True), ("export", True)]  # (módulo, recibe cfg)


def keep(r, cfg, kws):
    if r.get("area_m2") is not None and r["area_m2"] < cfg["min_area_m2"]:
        return False
    s = r.get("stratum")
    if s is not None and not cfg["min_stratum"] <= s <= cfg["max_stratum"]:
        return False
    text = db.norm(f"{r.get('title') or ''} {r.get('description') or ''}")
    return not any(k in text for k in kws)


def run_pipeline(conn, cfg):
    for name, with_cfg in PIPELINE:
        mod = f"scraper.{name}"
        if importlib.util.find_spec(mod) is None:
            print(f"[pipeline] {mod} no existe todavía, se omite")
            continue
        t = time.time()
        try:
            m = importlib.import_module(mod)
            m.run(conn, cfg) if with_cfg else m.run(conn)
            print(f"[pipeline] {name} ok ({time.time() - t:.1f}s)")
        except Exception:
            print(f"[pipeline] {name} ERROR", file=sys.stderr)
            traceback.print_exc()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    ap.add_argument("--sources", help="lista separada por comas (por defecto, las habilitadas)")
    ap.add_argument("--max-pages", type=int, help="tope de páginas por búsqueda (desarrollo)")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(open("config.yaml"))
    if args.max_pages:
        cfg["scraping"]["max_pages"] = args.max_pages
    names = args.sources.split(",") if args.sources else [n for n, s in cfg["sources"].items() if s.get("enabled")]
    kws = [db.norm(k) for k in cfg.get("exclude_keywords", [])]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    conn = db.connect()
    db.init(conn)
    http = Http(cfg["scraping"])

    raw_path = os.environ.get("SCRAPER_RAW")
    raw = json.load(open(raw_path)) if raw_path and os.path.exists(raw_path) else None
    fetched_all, ok, summary = [], 0, []

    for name in names:
        t0, req0, errors = time.time(), http.requests, []
        stats, removed, items = dict(new=0, updated=0, price_changed=0, reactivated=0), 0, []
        try:
            if raw is not None:
                items = [r for r in raw if r["source"] == name]
            else:
                src = importlib.import_module(f"scraper.sources.{name}")
                items = src.fetch(cfg, http, args.mode, db.known_ids(conn, name))
                fetched_all += items
        except Exception as e:  # una fuente caída no tumba la corrida
            errors.append(f"{type(e).__name__}: {e}")
            traceback.print_exc()

        if not errors:
            ok += bool(items)
            prev = db.last_full_count(conn, name) if args.mode == "full" else None
            guard = args.mode == "full" and (not items or (prev and len(items) < cfg["source_drop_guard"] * prev))
            if guard:
                errors.append(f"caída de volumen: {len(items)} vs {prev} en la última full; no se retira nada")
            passing = [r for r in items if keep(r, cfg, kws)]
            stats = db.upsert(conn, passing, now)
            if not guard:
                # ids vistos = todo lo extraído (también lo filtrado), para no retirar por un cambio de filtros
                removed = db.mark_missing(conn, name, {str(r["source_id"]) for r in items}, args.mode, now, cfg)
        secs = round(time.time() - t0, 1)
        db.record_run(conn, started_at=now, mode=args.mode, source=name, fetched=len(items),
                      requests=http.requests - req0, new=stats["new"], updated=stats["updated"],
                      removed=removed, errors=errors, seconds=secs)
        line = dict(source=name, fetched=len(items), **stats, removed=removed,
                    requests=http.requests - req0, seconds=secs, errors=errors)
        summary.append(line)
        print(json.dumps(line, ensure_ascii=False))

    if raw_path and raw is None:
        json.dump(fetched_all, open(raw_path, "w"), ensure_ascii=False)

    if ok:
        run_pipeline(conn, cfg)
    conn.close()
    if not ok:
        sys.exit("todas las fuentes fallaron")


if __name__ == "__main__":
    main()

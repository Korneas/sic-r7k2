"""Publicaciones manuales en una DB temporal (SIC_DB): `.venv/bin/python -m scraper.check_manual`."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="sic-check-manual-"))
os.environ.setdefault("SIC_DB", str(TMP / "t.db"))

from scraper import db, dedupe, export, manual  # noqa: E402

SECRET = "NOTA-PRIVADA-xyz"


def main():
    conn = db.connect()
    db.init(conn)
    path = TMP / "manual.json"
    item = dict(id="1b2c", status="active", notes=SECRET, created_at="2026-11-10T00:00:00Z", updated_at="x",
                property_type="casa", price=300_000_000, area_m2=100.0, lat=3.40, lng=-76.53, age_years=20,
                bedrooms=3, bathrooms=2, url=None, created_by="ana", updated_by="ana", photos=["https://img/1.jpg"], title="Casa", description="d")
    gone = dict(item, id="9z", price=200_000_000)

    assert manual.run(conn, "t0", str(TMP / "no.json")) is None  # sin archivo: no toca nada
    path.write_text(json.dumps([item, gone]))
    manual.run(conn, "2026-11-10T00:00:00Z", str(path))
    r = conn.execute("SELECT * FROM listing WHERE id='mn-1b2c'").fetchone()
    assert r["source"] == "manual" and r["status"] == "active" and r["geo_precision"] == "exact", dict(r)

    # cambio de precio -> price_change; "9z" desaparece del archivo -> retirada
    path.write_text(json.dumps([dict(item, price=280_000_000, updated_by="beto")]))
    manual.run(conn, "2026-11-12T00:00:00Z", str(path))
    pcs = [p for (p,) in conn.execute("SELECT price FROM price_change WHERE listing_id='mn-1b2c' ORDER BY seen_at")]
    assert pcs == [300_000_000, 280_000_000], pcs
    assert conn.execute("SELECT status FROM listing WHERE id='mn-9z'").fetchone()[0] == "removed"

    # un aviso de portal idéntico no se fusiona con la manual
    fr = {k: item.get(k) for k in manual.KEYS} | dict(source="fincaraiz", source_id="77", operation="sale", price=280_000_000)
    db.upsert(conn, [fr], "2026-11-12T00:00:00Z")
    dedupe.run(conn, {"dedupe": {"photo_budget": 0}})
    g = dict(conn.execute("SELECT id, group_id FROM listing WHERE status='active'").fetchall())
    assert g["mn-1b2c"] == "mn-1b2c" and g["fr-77"] == "fr-77", g

    out = TMP / "web-data"
    export.run(conn, {}, out_dir=out)
    ls = {l["id"]: l for l in json.loads((out / "listings.json").read_text())["listings"]}
    assert ls["mn-1b2c"]["src"] == ["mn"] and "mn-9z" not in ls, ls.keys()
    d = json.loads((out / "detail" / (hashlib.md5(b"mn-1b2c").hexdigest()[:2] + ".json")).read_text())["mn-1b2c"]
    assert d["by"] == "ana" and d["by_upd"] == "beto" and d["urls"] == [], d

    # sold -> retirada
    path.write_text(json.dumps([dict(item, status="sold", price=280_000_000)]))
    manual.run(conn, "2026-11-13T00:00:00Z", str(path))
    assert conn.execute("SELECT status FROM listing WHERE id='mn-1b2c'").fetchone()[0] == "removed"

    # notes nunca en la DB ni en el export
    conn.close()
    assert SECRET.encode() not in Path(os.environ["SIC_DB"]).read_bytes()
    assert not any(SECRET in f.read_text() for f in out.rglob("*.json")), "notes en el export"
    shutil.rmtree(TMP, ignore_errors=True)
    print("ok")


if __name__ == "__main__":
    main()

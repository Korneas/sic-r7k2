"""Cliente HTTP educado: pausa aleatoria entre requests y reintentos en 5xx/errores de red."""
import random
import time

import httpx


class Http:
    def __init__(self, cfg):
        self.c = httpx.Client(headers={"User-Agent": cfg["user_agent"], "Accept-Language": "es-CO,es;q=0.9"},
                              timeout=30, follow_redirects=True)
        self.delay = cfg["delay_seconds"]
        self.requests = 0

    def _req(self, url, polite=True, **kw):
        for attempt in range(3):
            if polite:
                time.sleep(random.uniform(*self.delay))
            self.requests += 1
            try:
                r = self.c.get(url, **kw)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r
            except httpx.TransportError:
                if attempt == 2:
                    raise
        r.raise_for_status()

    def get(self, url, **kw):
        return self._req(url, **kw).text

    def get_json(self, url, **kw):
        return self._req(url, **kw).json()

    def fetch(self, url):
        """Como get pero sin fallar en 4xx (un 404 es información, p. ej. anuncio retirado)."""
        time.sleep(random.uniform(*self.delay))
        self.requests += 1
        return self.c.get(url)

"""
Proxy manager — round-robin, dead tracking, async scrape.
"""

import asyncio
import aiohttp
import random
from config import PROXY_SOURCES


class ProxyManager:
    def __init__(self, proxies: list[str]):
        self._all   = list(dict.fromkeys(p.strip() for p in proxies if p.strip()))
        self._alive = list(self._all)
        self._dead  = set()
        self._idx   = 0
        self._lock  = asyncio.Lock()

    @classmethod
    def from_file(cls, path: str):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f if l.strip()]
        proxies = []
        for line in lines:
            line = line.replace("http://", "").replace("https://", "")
            proxies.append(f"http://{line}")
        return cls(proxies)

    @classmethod
    async def scrape(cls, urls=None):
        sources = urls or PROXY_SOURCES
        proxies = []
        connector = aiohttp.TCPConnector(ssl=False)
        timeout   = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            tasks = [s.get(u) for u in sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    continue
                try:
                    text = await r.text()
                    for line in text.splitlines():
                        line = line.strip()
                        if line and ":" in line:
                            line = line.replace("http://", "").replace("https://", "")
                            proxies.append(f"http://{line}")
                except Exception:
                    pass
        return cls(proxies)

    @property
    def total(self):  return len(self._all)
    @property
    def alive(self):  return len(self._alive)
    def has_proxies(self): return bool(self._alive)

    async def get(self) -> str | None:
        async with self._lock:
            if not self._alive:
                return None
            proxy = self._alive[self._idx % len(self._alive)]
            self._idx += 1
            return proxy

    async def mark_dead(self, proxy: str):
        async with self._lock:
            self._dead.add(proxy)
            self._alive = [p for p in self._alive if p not in self._dead]
            if not self._alive:
                # revive all — all dead is worse than retrying dead ones
                self._alive = list(self._all)
                self._dead.clear()

    async def get_fresh(self) -> str | None:
        """Return a random alive proxy different from recently used."""
        async with self._lock:
            if not self._alive:
                return None
            return random.choice(self._alive)

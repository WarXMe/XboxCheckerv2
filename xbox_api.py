"""
Xbox Live API chain — optimized for speed and accuracy.
All functions accept a pre-built aiohttp.ClientSession (per-worker, reused).
"""

import asyncio
import aiohttp
from config import T_AUTH, T_API, T_MC, T_CONNECT

# ─── Timeout builders ─────────────────────────────────────────────────────────
def _t(total, connect=None):
    return aiohttp.ClientTimeout(total=total, connect=connect or T_CONNECT)

# ─── 2FA detection keywords ───────────────────────────────────────────────────
_2FA_KEYS = [
    "safa", "SAFA", "two", "proof", "AADSTS50076", "mfa",
    "strongauthentication", "interaction_required", "verification",
    "challenge", "proofup", "otc", "phone", "authenticator",
]

# ─── Step 1: Microsoft Live Token ─────────────────────────────────────────────
async def get_live_token(session: aiohttp.ClientSession, email: str, password: str, proxy=None):
    url  = "https://login.live.com/oauth20_token.srf"
    data = (
        "grant_type=password"
        f"&username={email}"
        f"&password={password}"
        "&client_id=000000004C12AE6F"
        "&scope=service%3A%3Auser.auth.xboxlive.com%3A%3AMBI_SSL"
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        async with session.post(
            url, data=data, headers=headers,
            proxy=proxy, timeout=_t(T_AUTH),
            ssl=False, allow_redirects=False,
        ) as r:
            text = await r.text()
            if any(k in text for k in _2FA_KEYS):
                return "2fa"
            if "access_token" not in text:
                return None
            import json as _j
            j = _j.loads(text)
            return j.get("access_token")
    except (aiohttp.ClientProxyConnectionError, aiohttp.ServerConnectionError,
            asyncio.TimeoutError, aiohttp.ClientConnectorError):
        return "proxy_error"
    except Exception:
        return None


# ─── Step 2: Xbox UserToken ───────────────────────────────────────────────────
async def get_user_token(session: aiohttp.ClientSession, access_token: str, proxy=None):
    url  = "https://user.auth.xboxlive.com/user/authenticate"
    body = {
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": access_token,
        },
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        async with session.post(
            url, json=body, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False, allow_redirects=False,
        ) as r:
            j = await r.json(content_type=None)
            token = j.get("Token", "")
            uhs   = j.get("DisplayClaims", {}).get("xui", [{}])[0].get("uhs", "")
            return (token, uhs) if token else None
    except Exception:
        return None


# ─── Step 3: XSTS Token ───────────────────────────────────────────────────────
async def get_xsts(session: aiohttp.ClientSession, user_token: str, proxy=None,
                   relying_party: str = "http://xboxlive.com"):
    url  = "https://xsts.auth.xboxlive.com/xsts/authorize"
    body = {
        "RelyingParty": relying_party,
        "TokenType": "JWT",
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [user_token],
        },
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        async with session.post(
            url, json=body, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False, allow_redirects=False,
        ) as r:
            j = await r.json(content_type=None)
            return j.get("Token")
    except Exception:
        return None


# ─── Step 4: Profile (Gamertag, Tier, Rep, Tenure, XUID) ─────────────────────
async def get_profile(session: aiohttp.ClientSession, xsts: str, uhs: str, proxy=None):
    url = (
        "https://profile.xboxlive.com/users/me/profile/settings"
        "?settings=Gamertag,AccountTier,XboxOneRep,TenureLevel"
    )
    auth = f"XBL3.0 x={uhs};{xsts}"
    headers = {
        "Authorization": auth,
        "x-xbl-contract-version": "2",
        "Accept": "application/json",
    }
    try:
        async with session.get(
            url, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False,
        ) as r:
            j = await r.json(content_type=None)
            users = j.get("profileUsers", [{}])[0]
            xuid  = users.get("id", "N/A")
            smap  = {s["id"]: s["value"] for s in users.get("settings", [])}
            return xuid, smap
    except Exception:
        return None, {}


# ─── Step 5: Gamerscore ───────────────────────────────────────────────────────
async def get_gscore(session: aiohttp.ClientSession, xsts: str, uhs: str, proxy=None):
    url  = "https://profile.xboxlive.com/users/me/summary"
    auth = f"XBL3.0 x={uhs};{xsts}"
    headers = {"Authorization": auth, "x-xbl-contract-version": "2"}
    try:
        async with session.get(
            url, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False,
        ) as r:
            j = await r.json(content_type=None)
            return j.get("gamerscore", 0)
    except Exception:
        return 0


# ─── Step 6: Games list ───────────────────────────────────────────────────────
async def get_games(session: aiohttp.ClientSession, xsts: str, uhs: str, proxy=None):
    url  = "https://titlehub.xboxlive.com/users/me/titles/titlehistory/decoration/achievement,image,detail"
    auth = f"XBL3.0 x={uhs};{xsts}"
    headers = {"Authorization": auth, "x-xbl-contract-version": "2"}
    try:
        async with session.get(
            url, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False,
        ) as r:
            j = await r.json(content_type=None)
            titles = j.get("titles", [])
            out = []
            for t in titles[:50]:
                name  = t.get("name", "")
                score = 0
                for ach in t.get("achievement", {}).get("progressState", []):
                    pass
                # get earned gamerscore from achievements
                ach_info = t.get("achievement", {})
                score = ach_info.get("currentGamerscore", 0)
                if name:
                    out.append((name, score))
            return out
    except Exception:
        return []


# ─── Step 7: Subscriptions ────────────────────────────────────────────────────
_SUB_MAP = {
    "CFQ7TTC0K6L8": "Game Pass Ultimate",
    "CFQ7TTC0K6L9": "Game Pass Console",
    "CFQ7TTC0KGQ8": "PC Game Pass",
}

async def get_subscriptions(session: aiohttp.ClientSession, xsts: str, uhs: str, proxy=None):
    url  = "https://licensing.xboxlive.com/v8.0/licenses/query"
    auth = f"XBL3.0 x={uhs};{xsts}"
    body = {"skuIds": list(_SUB_MAP.keys())}
    headers = {
        "Authorization": auth,
        "x-xbl-contract-version": "8",
        "Content-Type": "application/json",
    }
    try:
        async with session.post(
            url, json=body, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False,
        ) as r:
            j = await r.json(content_type=None)
            subs = []
            for item in j.get("items", []):
                sku = item.get("skuId", "")
                if sku in _SUB_MAP and item.get("satisfiedBySubscriptions"):
                    subs.append(_SUB_MAP[sku])
            return subs
    except Exception:
        return []


# ─── Step 8: Minecraft Java (full 4-step chain) ───────────────────────────────
async def get_mc_java_profile(session: aiohttp.ClientSession, access_token: str, proxy=None):
    """Returns dict{name, uuid, capes} or None."""
    try:
        # XBL token (separate from Xbox one)
        ut_result = await get_user_token(session, access_token, proxy)
        if not ut_result:
            return None
        mc_user_token, mc_uhs = ut_result

        # XSTS for Minecraft
        mc_xsts = await get_xsts(session, mc_user_token, proxy,
                                  relying_party="rp://api.minecraftservices.com/")
        if not mc_xsts:
            return None

        # MC bearer
        mc_body = {"identityToken": f"XBL3.0 x={mc_uhs};{mc_xsts}"}
        async with session.post(
            "https://api.minecraftservices.com/authentication/login_with_xbox",
            json=mc_body, timeout=_t(T_MC), ssl=False,
            proxy=proxy,
        ) as r:
            mc_j = await r.json(content_type=None)
            mc_bearer = mc_j.get("access_token")
        if not mc_bearer:
            return None

        # MC profile
        async with session.get(
            "https://api.minecraftservices.com/minecraft/profile",
            headers={"Authorization": f"Bearer {mc_bearer}"},
            timeout=_t(T_MC), ssl=False, proxy=proxy,
        ) as r:
            pj = await r.json(content_type=None)
            if "error" in pj or "name" not in pj:
                return None
            capes = [c.get("alias", "") for c in pj.get("capes", []) if c.get("alias")]
            return {
                "name":  pj.get("name", "N/A"),
                "uuid":  pj.get("id",   "N/A"),
                "capes": ", ".join(capes) if capes else "None",
            }
    except Exception:
        return None


# ─── Step 9: Xbox MC Cape check ───────────────────────────────────────────────
async def get_mc_cape_xbox(session: aiohttp.ClientSession, xsts: str, uhs: str,
                            xuid: str, proxy=None):
    url  = f"https://profile.xboxlive.com/users/xuid({xuid})/settings/MinecraftCape"
    auth = f"XBL3.0 x={uhs};{xsts}"
    headers = {"Authorization": auth, "x-xbl-contract-version": "2"}
    try:
        async with session.get(
            url, headers=headers,
            proxy=proxy, timeout=_t(T_API),
            ssl=False,
        ) as r:
            return r.status == 200
    except Exception:
        return False

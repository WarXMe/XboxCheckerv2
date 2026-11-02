import os

# ─── Bot Settings ─────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "https://your-app.onrender.com")  # your render URL
WEBHOOK_PATH = "/webhook"
PORT         = int(os.getenv("PORT", 10000))

# ─── Admin / Branding ─────────────────────────────────────────────────────────
ADMIN_IDS    = [int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",") if x.strip()]
CAPTURED_BY  = os.getenv("CAPTURED_BY", "@YourUsername")
BOT_VERSION  = "2.0"

# ─── Checker Settings ─────────────────────────────────────────────────────────
DEFAULT_THREADS = int(os.getenv("DEFAULT_THREADS", "100"))
MAX_THREADS     = 200
MAX_COMBO_MB    = 500

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR  = os.path.join(BASE_DIR, "results")
DATA_DIR     = os.path.join(BASE_DIR, "data")
STATS_FILE   = os.path.join(DATA_DIR, "stats.json")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ─── Proxy Sources ────────────────────────────────────────────────────────────
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt",
]

# ─── Timeouts (seconds) ───────────────────────────────────────────────────────
T_AUTH    = 10   # login.live.com auth
T_API     = 12   # xbox api calls
T_MC      = 15   # minecraft profile
T_CONNECT = 5    # tcp connect timeout

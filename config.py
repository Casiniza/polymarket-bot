import os
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY      = os.getenv("PRIVATE_KEY", "")
DEPOSIT_WALLET   = os.getenv("DEPOSIT_WALLET", "")
CLOB_API_KEY     = os.getenv("CLOB_API_KEY", "")
CLOB_API_SECRET  = os.getenv("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

MAX_BET_USDC         = float(os.getenv("MAX_BET_USDC", "5.0"))
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE", "0.65"))
DRY_RUN              = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_DAILY_LOSS_USDC  = float(os.getenv("MAX_DAILY_LOSS_USDC", "15.0"))
MIN_MARKET_VOLUME    = float(os.getenv("MIN_MARKET_VOLUME", "10000.0"))

# Estrategias activas (separadas por coma): SAFE_BET,MOMENTUM
STRATEGIES_ACTIVE    = [s.strip() for s in os.getenv("STRATEGIES_ACTIVE", "SAFE_BET,MOMENTUM").split(",")]
SAFE_BET_MIN         = float(os.getenv("SAFE_BET_MIN", "0.78"))
SAFE_BET_MAX         = float(os.getenv("SAFE_BET_MAX", "0.92"))
THRESHOLD_BUY_YES    = float(os.getenv("THRESHOLD_BUY_YES", "0.30"))
THRESHOLD_BUY_NO     = float(os.getenv("THRESHOLD_BUY_NO", "0.30"))

# Filtro de estabilidad de precio
MAX_PRICE_VOLATILITY = float(os.getenv("MAX_PRICE_VOLATILITY", "0.08"))  # máx 8% de variación en 30 min

# Mundial — detección y ajuste automático
WC_BET_USDC          = float(os.getenv("WC_BET_USDC", "8.0"))
WC_SAFE_BET_MIN      = float(os.getenv("WC_SAFE_BET_MIN", "0.75"))
WC_SAFE_BET_MAX      = float(os.getenv("WC_SAFE_BET_MAX", "0.95"))

# Paper trading (simulación paralela)
PAPER_TRADING        = os.getenv("PAPER_TRADING", "true").lower() == "true"

CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

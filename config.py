import os
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY      = os.getenv("PRIVATE_KEY", "")
DEPOSIT_WALLET   = os.getenv("DEPOSIT_WALLET", "")
CLOB_API_KEY     = os.getenv("CLOB_API_KEY", "")
CLOB_API_SECRET  = os.getenv("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

MAX_BET_USDC         = float(os.getenv("MAX_BET_USDC", "4.0"))     # techo máximo por apuesta
MIN_BET_USDC         = float(os.getenv("MIN_BET_USDC", "1.5"))     # mínimo para que valga la pena
BET_PCT_BALANCE      = float(os.getenv("BET_PCT_BALANCE", "0.25")) # apostar hasta el 25% del balance
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE", "0.72"))   # señales más limpias
DRY_RUN              = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_DAILY_LOSS_USDC  = float(os.getenv("MAX_DAILY_LOSS_USDC", "10.0"))  # límite diario más conservador
# Volumen mínimo $8k — equilibrio entre liquidez y oportunidades
MIN_MARKET_VOLUME    = float(os.getenv("MIN_MARKET_VOLUME", "8000.0"))

# Estrategias activas (separadas por coma)
STRATEGIES_ACTIVE    = [s.strip() for s in os.getenv("STRATEGIES_ACTIVE", "SAFE_BET,ALWAYS_NO").split(",")]

# Rango SAFE_BET: 0.55-0.88
# - Mín bajado a 0.55: captura favoritos moderados con margen de subida
# - Máx 0.88: límite matemático para TP +10% (0.88*1.10=0.968 < 0.99) ✓
SAFE_BET_MIN         = float(os.getenv("SAFE_BET_MIN", "0.55"))
SAFE_BET_MAX         = float(os.getenv("SAFE_BET_MAX", "0.88"))

THRESHOLD_BUY_YES    = float(os.getenv("THRESHOLD_BUY_YES", "0.30"))
THRESHOLD_BUY_NO     = float(os.getenv("THRESHOLD_BUY_NO", "0.30"))

# Filtro de estabilidad de precio
MAX_PRICE_VOLATILITY = float(os.getenv("MAX_PRICE_VOLATILITY", "0.10"))  # máx 10% de variación en 30 min

# Mundial — detección y ajuste automático
WC_BET_USDC          = float(os.getenv("WC_BET_USDC", "8.0"))
WC_SAFE_BET_MIN      = float(os.getenv("WC_SAFE_BET_MIN", "0.70"))
WC_SAFE_BET_MAX      = float(os.getenv("WC_SAFE_BET_MAX", "0.88"))

# Paper trading (simulación paralela)
PAPER_TRADING            = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_SAFE_BET_MIN       = float(os.getenv("PAPER_SAFE_BET_MIN", "0.55"))
PAPER_SAFE_BET_MAX       = float(os.getenv("PAPER_SAFE_BET_MAX", "0.88"))
PAPER_BET_USDC           = float(os.getenv("PAPER_BET_USDC", "5.0"))
PAPER_HIGH_CONF_THRESHOLD = float(os.getenv("PAPER_HIGH_CONF_THRESHOLD", "0.82"))
PAPER_HIGH_CONF_BET      = float(os.getenv("PAPER_HIGH_CONF_BET", "10.0"))

CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

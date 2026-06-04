import os
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
DEPOSIT_WALLET = os.getenv("DEPOSIT_WALLET", "")
CLOB_API_KEY = os.getenv("CLOB_API_KEY", "")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

MAX_BET_USDC = float(os.getenv("MAX_BET_USDC", "10.0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.65"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_DAILY_LOSS_USDC = float(os.getenv("MAX_DAILY_LOSS_USDC", "15.0"))
MIN_MARKET_VOLUME = float(os.getenv("MIN_MARKET_VOLUME", "10000.0"))

STRATEGY = os.getenv("STRATEGY", "SAFE_BET")
THRESHOLD_BUY_YES = float(os.getenv("THRESHOLD_BUY_YES", "0.30"))
THRESHOLD_BUY_NO = float(os.getenv("THRESHOLD_BUY_NO", "0.30"))
SAFE_BET_MIN = float(os.getenv("SAFE_BET_MIN", "0.78"))
SAFE_BET_MAX = float(os.getenv("SAFE_BET_MAX", "0.92"))

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

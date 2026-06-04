import os
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CLOB_API_KEY = os.getenv("CLOB_API_KEY", "")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE", "")

MAX_BET_USDC = float(os.getenv("MAX_BET_USDC", "10.0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.65"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

STRATEGY = os.getenv("STRATEGY", "THRESHOLD")
THRESHOLD_BUY_YES = float(os.getenv("THRESHOLD_BUY_YES", "0.30"))
THRESHOLD_BUY_NO = float(os.getenv("THRESHOLD_BUY_NO", "0.30"))

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

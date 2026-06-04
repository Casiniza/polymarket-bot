"""
Estrategias de trading.
- Soporta múltiples estrategias simultáneas: elige la de mayor confianza.
- Detección automática del Mundial para ajustar parámetros.
"""
from dataclasses import dataclass
from loguru import logger
import config


@dataclass
class Signal:
    action: str          # "BUY_YES" | "BUY_NO" | "HOLD"
    confidence: float    # 0.0 - 1.0
    reason: str
    token_id: str
    price: float
    strategy: str = ""   # nombre de la estrategia que generó la señal


# --- Detección Mundial ---
WC_KEYWORDS = ["world cup", "fifa", "mundial", "coupe du monde"]

def is_world_cup(market: dict) -> bool:
    q = market.get("question", "").lower()
    return any(kw in q for kw in WC_KEYWORDS)

def get_safe_range(market: dict, paper: bool = False) -> tuple[float, float]:
    """Devuelve el rango de precio para safe bet."""
    if paper:
        return config.PAPER_SAFE_BET_MIN, config.PAPER_SAFE_BET_MAX
    if is_world_cup(market):
        return config.WC_SAFE_BET_MIN, config.WC_SAFE_BET_MAX
    return config.SAFE_BET_MIN, config.SAFE_BET_MAX

def get_bet_size(market: dict, paper: bool = False) -> float:
    """Devuelve el tamaño de apuesta."""
    if paper:
        return config.PAPER_BET_USDC
    if is_world_cup(market):
        logger.info(f"⚽ Mundial detectado — apuesta aumentada a ${config.WC_BET_USDC}")
        return config.WC_BET_USDC
    return config.MAX_BET_USDC


# --- Estrategias ---

def threshold_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    t_yes = config.THRESHOLD_BUY_YES
    t_no  = config.THRESHOLD_BUY_NO

    if yes_price and yes_price < t_yes and token_id_yes:
        confidence = min(1.0, (t_yes - yes_price) / t_yes + 0.5)
        return Signal("BUY_YES", confidence, f"YES {yes_price:.2f} < {t_yes}", token_id_yes, yes_price, "THRESHOLD")
    if no_price and no_price < t_no and token_id_no:
        confidence = min(1.0, (t_no - no_price) / t_no + 0.5)
        return Signal("BUY_NO", confidence, f"NO {no_price:.2f} < {t_no}", token_id_no, no_price, "THRESHOLD")
    return Signal("HOLD", 0.0, "Sin oportunidad", token_id_yes or "", yes_price or 0.0, "THRESHOLD")


def momentum_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Detecta momentum usando el historial de precios del mercado.
    Si el precio ha subido consistentemente en la última hora, compra en esa dirección.
    """
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    if not yes_price or not no_price:
        return Signal("HOLD", 0.0, "Datos insuficientes", "", 0.0, "MOMENTUM")

    price_history = market.get("_price_history", [])  # lista de precios recientes [más antiguo ... más nuevo]

    if len(price_history) >= 3:
        # Comprueba tendencia: si los últimos 3 precios son ascendentes/descendentes
        trend = price_history[-1] - price_history[0]
        if trend > 0.05 and yes_price > 0.5 and token_id_yes:
            confidence = min(0.95, 0.65 + trend)
            return Signal("BUY_YES", confidence, f"Momentum YES +{trend:.2f} en {len(price_history)} obs.", token_id_yes, yes_price, "MOMENTUM")
        if trend < -0.05 and no_price > 0.5 and token_id_no:
            confidence = min(0.95, 0.65 + abs(trend))
            return Signal("BUY_NO", confidence, f"Momentum NO {trend:.2f} en {len(price_history)} obs.", token_id_no, no_price, "MOMENTUM")

    # Fallback: spread como proxy de momentum
    spread = abs(yes_price - no_price)
    if yes_price > no_price and spread > 0.15 and token_id_yes:
        return Signal("BUY_YES", min(spread, 0.9), f"Spread momentum YES ({spread:.2f})", token_id_yes, yes_price, "MOMENTUM")
    if no_price > yes_price and spread > 0.15 and token_id_no:
        return Signal("BUY_NO", min(spread, 0.9), f"Spread momentum NO ({spread:.2f})", token_id_no, no_price, "MOMENTUM")

    return Signal("HOLD", 0.0, "Sin momentum claro", token_id_yes or "", yes_price, "MOMENTUM")


def contrarian_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    if yes_price and yes_price > 0.85 and token_id_no:
        confidence = (yes_price - 0.85) / 0.15
        return Signal("BUY_NO", confidence, f"YES sobrecomprado {yes_price:.2f}", token_id_no, 1 - yes_price, "CONTRARIAN")
    if yes_price and yes_price < 0.15 and token_id_yes:
        confidence = (0.15 - yes_price) / 0.15
        return Signal("BUY_YES", confidence, f"YES sobrevendido {yes_price:.2f}", token_id_yes, yes_price, "CONTRARIAN")
    return Signal("HOLD", 0.0, "Sin extremo", token_id_yes or "", yes_price or 0.0, "CONTRARIAN")


def safe_bet_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    paper = market.get("_paper", False)
    s_min, s_max = get_safe_range(market, paper)

    if yes_price and s_min <= yes_price <= s_max and token_id_yes:
        confidence = 0.7 + 0.3 * (yes_price - s_min) / (s_max - s_min)
        return Signal("BUY_YES", confidence, f"Safe bet YES {yes_price:.2f} ({s_min}-{s_max})", token_id_yes, yes_price, "SAFE_BET")
    if no_price and s_min <= no_price <= s_max and token_id_no:
        confidence = 0.7 + 0.3 * (no_price - s_min) / (s_max - s_min)
        return Signal("BUY_NO", confidence, f"Safe bet NO {no_price:.2f} ({s_min}-{s_max})", token_id_no, no_price, "SAFE_BET")
    return Signal("HOLD", 0.0, "Fuera del rango seguro", token_id_yes or "", yes_price or 0.0, "SAFE_BET")


STRATEGIES = {
    "THRESHOLD":  threshold_strategy,
    "MOMENTUM":   momentum_strategy,
    "CONTRARIAN": contrarian_strategy,
    "SAFE_BET":   safe_bet_strategy,
}


def evaluate(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Evalúa todas las estrategias activas y devuelve la señal de mayor confianza.
    """
    best = Signal("HOLD", 0.0, "Sin señal", "", yes_price or 0.0)

    for name in config.STRATEGIES_ACTIVE:
        fn = STRATEGIES.get(name)
        if not fn:
            continue
        signal = fn(market, yes_price, no_price)
        if signal.action != "HOLD" and signal.confidence > best.confidence:
            best = signal

    if best.action != "HOLD" and best.confidence < config.MIN_CONFIDENCE:
        logger.debug(f"Señal descartada (confianza {best.confidence:.2f} < {config.MIN_CONFIDENCE}): {best.reason}")
        best.action = "HOLD"
    elif best.action != "HOLD":
        logger.debug(f"Señal [{best.strategy}] confianza {best.confidence:.2f}: {best.reason}")

    return best


def _get_token_id(market: dict, outcome: str) -> str | None:
    tokens = market.get("tokens") or []
    for t in tokens:
        if isinstance(t, dict) and t.get("outcome", "").upper() == outcome:
            return t.get("token_id") or t.get("tokenId")
    clob_ids = market.get("clobTokenIds") or []
    if isinstance(clob_ids, list) and len(clob_ids) >= 2:
        if outcome == "YES":
            return clob_ids[0] if isinstance(clob_ids[0], str) else None
        if outcome == "NO":
            return clob_ids[1] if isinstance(clob_ids[1], str) else None
    return None

"""Estrategias de trading. Cada una devuelve una señal: BUY_YES, BUY_NO o HOLD."""
from dataclasses import dataclass
from loguru import logger
from config import STRATEGY, THRESHOLD_BUY_YES, THRESHOLD_BUY_NO, MIN_CONFIDENCE


@dataclass
class Signal:
    action: str          # "BUY_YES" | "BUY_NO" | "HOLD"
    confidence: float    # 0.0 - 1.0
    reason: str
    token_id: str
    price: float


def threshold_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Compra YES si el precio es anormalmente bajo (mercado subestima el evento)
    o NO si el precio de YES es anormalmente alto.
    """
    token_id_yes = _get_token_id(market, "YES")
    token_id_no = _get_token_id(market, "NO")

    if yes_price and yes_price < THRESHOLD_BUY_YES and token_id_yes:
        confidence = min(1.0, (THRESHOLD_BUY_YES - yes_price) / THRESHOLD_BUY_YES + 0.5)
        return Signal("BUY_YES", confidence, f"YES price {yes_price:.2f} < threshold {THRESHOLD_BUY_YES}", token_id_yes, yes_price)

    if no_price and no_price < THRESHOLD_BUY_NO and token_id_no:
        confidence = min(1.0, (THRESHOLD_BUY_NO - no_price) / THRESHOLD_BUY_NO + 0.5)
        return Signal("BUY_NO", confidence, f"NO price {no_price:.2f} < threshold {THRESHOLD_BUY_NO}", token_id_no, no_price)

    return Signal("HOLD", 0.0, "Sin oportunidad", token_id_yes or "", yes_price or 0.0)


def momentum_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Compra en la dirección del momentum reciente.
    Requiere histórico de precios — aquí usa la diferencia bid/ask como proxy.
    """
    token_id_yes = _get_token_id(market, "YES")
    if not yes_price or not no_price or not token_id_yes:
        return Signal("HOLD", 0.0, "Datos insuficientes", "", 0.0)

    spread = abs(yes_price - no_price)
    # Si spread es grande, hay momentum en la dirección dominante
    if yes_price > no_price and spread > 0.1:
        return Signal("BUY_YES", min(spread, 1.0), f"Momentum YES (spread {spread:.2f})", token_id_yes, yes_price)
    if no_price > yes_price and spread > 0.1:
        token_id_no = _get_token_id(market, "NO")
        return Signal("BUY_NO", min(spread, 1.0), f"Momentum NO (spread {spread:.2f})", token_id_no or "", no_price)

    return Signal("HOLD", 0.0, "Sin momentum claro", token_id_yes, yes_price)


def contrarian_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """Va en contra del mercado cuando el consenso parece extremo."""
    token_id_yes = _get_token_id(market, "YES")
    token_id_no = _get_token_id(market, "NO")

    if yes_price and yes_price > 0.85 and token_id_no:
        confidence = (yes_price - 0.85) / 0.15
        return Signal("BUY_NO", confidence, f"YES sobrecomprado en {yes_price:.2f}", token_id_no, 1 - yes_price)

    if yes_price and yes_price < 0.15 and token_id_yes:
        confidence = (0.15 - yes_price) / 0.15
        return Signal("BUY_YES", confidence, f"YES sobrevendido en {yes_price:.2f}", token_id_yes, yes_price)

    return Signal("HOLD", 0.0, "Sin extremo detectable", token_id_yes or "", yes_price or 0.0)


STRATEGIES = {
    "THRESHOLD": threshold_strategy,
    "MOMENTUM": momentum_strategy,
    "CONTRARIAN": contrarian_strategy,
}


def evaluate(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    fn = STRATEGIES.get(STRATEGY, threshold_strategy)
    signal = fn(market, yes_price, no_price)
    if signal.confidence < MIN_CONFIDENCE:
        logger.debug(f"Señal descartada (confianza {signal.confidence:.2f} < {MIN_CONFIDENCE}): {signal.reason}")
        signal.action = "HOLD"
    return signal


def _get_token_id(market: dict, outcome: str) -> str | None:
    tokens = market.get("tokens") or market.get("clobTokenIds") or []
    for t in tokens:
        if isinstance(t, dict) and t.get("outcome", "").upper() == outcome:
            return t.get("token_id") or t.get("tokenId")
    return None

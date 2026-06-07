"""
Estrategias de trading.
- Soporta múltiples estrategias: elige la de mayor confianza.
- Detección automática del Mundial para ajustar parámetros.
- Dynamic Position Sizing para paper trading basado en P&L reciente.

Estrategias activas por defecto: SAFE_BET + ALWAYS_NO
- SAFE_BET:   apuesta al favorito claro (0.55–0.88) en mercados deportivos.
- ALWAYS_NO:  aprovecha que el 73.4% de mercados Polymarket resuelven NO.
              Solo en paper para mercados no deportivos (más volumen, más señales).
- MOMENTUM:   DESACTIVADO por defecto — requiere historial de 30min y el fallback
              de spread era peligroso (generó las posiciones Washington Mystics).
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from loguru import logger
import config


@dataclass
class Signal:
    action: str          # "BUY_YES" | "BUY_NO" | "HOLD"
    confidence: float    # 0.0 - 1.0
    reason: str
    token_id: str
    price: float
    strategy: str = ""


# --- Detección Mundial ---
WC_KEYWORDS = ["world cup", "fifa", "mundial", "coupe du monde"]

def is_world_cup(market: dict) -> bool:
    q = market.get("question", "").lower()
    return any(kw in q for kw in WC_KEYWORDS)

def get_safe_range(market: dict, paper: bool = False) -> tuple[float, float]:
    if paper:
        return config.PAPER_SAFE_BET_MIN, config.PAPER_SAFE_BET_MAX
    if is_world_cup(market):
        return config.WC_SAFE_BET_MIN, config.WC_SAFE_BET_MAX
    return config.SAFE_BET_MIN, config.SAFE_BET_MAX


def get_dynamic_paper_bet(price: float = 0.0) -> float:
    """
    Dynamic Position Sizing para paper trading.
    - Racha buena  (P&L 3h > +$2)  → $10
    - Neutro                        → $5
    - Racha mala   (P&L 3h < -$2)  → $3
    """
    try:
        from positions import load_history
        history = load_history(paper=True)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
        recent_pnl = sum(
            h.get("pnl", 0) for h in history
            if h.get("result") != "GHOST" and  # GHOSTs no son reales
            datetime.fromisoformat(h.get("closed_at", "2000-01-01")).replace(tzinfo=timezone.utc) >= cutoff
        )
    except Exception:
        recent_pnl = 0.0

    if price >= config.PAPER_HIGH_CONF_THRESHOLD:
        logger.info(f"[PAPER] Alta confianza precio {price:.2f} → apuesta max ${config.PAPER_HIGH_CONF_BET}")
        return config.PAPER_HIGH_CONF_BET

    if recent_pnl > 2.0:
        logger.info(f"[PAPER] Racha buena (P&L 3h: +${recent_pnl:.2f}) → apuesta ${config.PAPER_HIGH_CONF_BET}")
        return config.PAPER_HIGH_CONF_BET
    elif recent_pnl < -2.0:
        size = max(config.PAPER_BET_USDC * 0.6, 3.0)
        logger.info(f"[PAPER] Racha mala (P&L 3h: -${abs(recent_pnl):.2f}) → apuesta reducida ${size:.1f}")
        return size
    return config.PAPER_BET_USDC


def get_bet_size(market: dict, paper: bool = False, price: float = 0.0) -> float:
    if paper:
        return get_dynamic_paper_bet(price)
    if is_world_cup(market):
        logger.info(f"[Mundial] apuesta aumentada a ${config.WC_BET_USDC}")
        return config.WC_BET_USDC
    return config.MAX_BET_USDC


# ---------------------------------------------------------------------------
# Estrategias
# ---------------------------------------------------------------------------

def safe_bet_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Apuesta al favorito claro: precio entre SAFE_BET_MIN y SAFE_BET_MAX.
    Prioriza el token con precio más alto dentro del rango (mayor confianza).
    Excluye precios > MAX_ENTRY donde el TP es matemáticamente inalcanzable.
    """
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    paper = market.get("_paper", False)
    s_min, s_max = get_safe_range(market, paper)

    best = Signal("HOLD", 0.0, "Fuera del rango seguro", token_id_yes or "", yes_price or 0.0, "SAFE_BET")

    if yes_price and s_min <= yes_price <= s_max and token_id_yes:
        conf = 0.7 + 0.3 * (yes_price - s_min) / (s_max - s_min)
        sig = Signal("BUY_YES", conf,
                     f"Safe bet YES {yes_price:.3f} (rango {s_min}-{s_max})",
                     token_id_yes, yes_price, "SAFE_BET")
        if sig.confidence > best.confidence:
            best = sig

    if no_price and s_min <= no_price <= s_max and token_id_no:
        conf = 0.7 + 0.3 * (no_price - s_min) / (s_max - s_min)
        sig = Signal("BUY_NO", conf,
                     f"Safe bet NO {no_price:.3f} (rango {s_min}-{s_max})",
                     token_id_no, no_price, "SAFE_BET")
        if sig.confidence > best.confidence:
            best = sig

    return best


def always_no_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    'Nothing Ever Happens' — el 73.4% de mercados Polymarket resuelven en NO.
    Compra NO cuando su precio está entre 0.55 y 0.75.
    Cuanto más bajo el precio del NO (menos creído), mejor valor esperado.
    """
    token_id_no = _get_token_id(market, "NO")
    if not token_id_no or no_price is None:
        return Signal("HOLD", 0.0, "Sin token NO", "", 0.0, "ALWAYS_NO")

    NO_MIN, NO_MAX = 0.55, 0.75

    if NO_MIN <= no_price <= NO_MAX:
        # Mayor confianza cuanto más bajo el precio (más subvalorado)
        confidence = 0.70 + 0.15 * (NO_MAX - no_price) / (NO_MAX - NO_MIN)
        return Signal("BUY_NO", confidence,
                      f"Always NO: {no_price:.3f} en rango {NO_MIN}-{NO_MAX} (base rate 73.4%)",
                      token_id_no, no_price, "ALWAYS_NO")

    return Signal("HOLD", 0.0, "NO fuera del rango base rate", "", no_price or 0.0, "ALWAYS_NO")


def momentum_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """
    Solo actúa con historial real de precio (≥3 observaciones = 10+ min corriendo).
    SIN fallback de spread — ese fallback generaba entradas en mercados ya resueltos.
    """
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    if not yes_price or not no_price:
        return Signal("HOLD", 0.0, "Datos insuficientes", "", 0.0, "MOMENTUM")

    price_history = market.get("_price_history", [])
    if len(price_history) < 3:
        return Signal("HOLD", 0.0, "Sin historial suficiente (necesita 3+ obs.)", "", yes_price, "MOMENTUM")

    trend = price_history[-1] - price_history[0]
    s_min = config.SAFE_BET_MIN
    s_max = config.SAFE_BET_MAX

    if trend > 0.05 and s_min <= yes_price <= s_max and token_id_yes:
        confidence = min(0.90, 0.65 + trend * 2)
        return Signal("BUY_YES", confidence,
                      f"Momentum YES +{trend:.3f} ({len(price_history)} obs.)",
                      token_id_yes, yes_price, "MOMENTUM")

    if trend < -0.05 and s_min <= no_price <= s_max and token_id_no:
        confidence = min(0.90, 0.65 + abs(trend) * 2)
        return Signal("BUY_NO", confidence,
                      f"Momentum NO {trend:.3f} ({len(price_history)} obs.)",
                      token_id_no, no_price, "MOMENTUM")

    return Signal("HOLD", 0.0, "Sin momentum claro", token_id_yes or "", yes_price, "MOMENTUM")


def threshold_strategy(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """Apuesta cuando el precio está muy bajo (mercado sobreestimando el NO/YES contrario)."""
    token_id_yes = _get_token_id(market, "YES")
    token_id_no  = _get_token_id(market, "NO")
    t_yes = config.THRESHOLD_BUY_YES
    t_no  = config.THRESHOLD_BUY_NO

    if yes_price and yes_price < t_yes and token_id_yes:
        confidence = min(1.0, (t_yes - yes_price) / t_yes + 0.5)
        return Signal("BUY_YES", confidence, f"YES {yes_price:.3f} < {t_yes}", token_id_yes, yes_price, "THRESHOLD")
    if no_price and no_price < t_no and token_id_no:
        confidence = min(1.0, (t_no - no_price) / t_no + 0.5)
        return Signal("BUY_NO", confidence, f"NO {no_price:.3f} < {t_no}", token_id_no, no_price, "THRESHOLD")
    return Signal("HOLD", 0.0, "Sin oportunidad", token_id_yes or "", yes_price or 0.0, "THRESHOLD")


STRATEGIES = {
    "SAFE_BET":   safe_bet_strategy,
    "ALWAYS_NO":  always_no_strategy,
    "MOMENTUM":   momentum_strategy,
    "THRESHOLD":  threshold_strategy,
}


def evaluate(market: dict, yes_price: float | None, no_price: float | None) -> Signal:
    """Evalúa todas las estrategias activas y devuelve la señal de mayor confianza."""
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
        best = Signal("HOLD", 0.0, best.reason, best.token_id, best.price, best.strategy)
    elif best.action != "HOLD":
        logger.debug(f"Señal [{best.strategy}] conf={best.confidence:.2f}: {best.reason}")

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

"""Ejecuta órdenes de compra y venta en Polymarket via py-clob-client-v2.

Principios de ejecución (dinero real Y paper):
- El precio de la señal viene de outcomePrices (Gamma) que puede llevar MINUTOS
  de retraso en partidos en vivo. El precio que de verdad pagas es el ask del CLOB.
  Toda entrada se valida y registra contra el libro real, nunca contra Gamma.
- Órdenes FOK (Fill-Or-Kill): o se llenan entera e inmediatamente, o el exchange
  las cancela. Nunca quedan órdenes GTC huérfanas vivas en el libro, y nunca se
  registra una posición que no se llenó de verdad.
"""
import math
from loguru import logger
from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions, Side
from py_clob_client_v2.constants import POLYGON

import config
from strategy import Signal, get_bet_size
from positions import Position, add_position, remove_position
from markets import get_best_bid_ask

MAX_SPREAD    = 0.03   # 3¢ absolutos — compramos el ask: cada ¢ de spread es coste directo
                       # contra un TP de +7%; los deportes líquidos pre-partido van a 1-2¢
MAX_STALE_GAP = 0.04   # si el ask real difiere >4¢ de la señal, la señal está desfasada


def build_client() -> ClobClient:
    return ClobClient(
        host=config.CLOB_HOST,
        key=config.PRIVATE_KEY,
        chain_id=POLYGON,
        signature_type=3,
        funder=config.DEPOSIT_WALLET,
        creds=ApiCreds(
            api_key=config.CLOB_API_KEY,
            api_secret=config.CLOB_API_SECRET,
            api_passphrase=config.CLOB_API_PASSPHRASE,
        ),
    )


def _get_tick(market: dict) -> float:
    tick_str = str(market.get("orderPriceMinTickSize", "0.01")) if market else "0.01"
    if tick_str not in {"0.1", "0.01", "0.001", "0.0001"}:
        tick_str = "0.01"
    return float(tick_str)


def _options(market: dict) -> PartialCreateOrderOptions:
    tick = _get_tick(market)
    return PartialCreateOrderOptions(tick_size=str(tick))


def _snap_up(price: float, tick: float) -> float:
    """Redondea HACIA ARRIBA al tick (mín 0.01) — para compras marketables que crucen el ask."""
    t = max(tick, 0.01)
    snapped = round(math.ceil(round(price / t, 6)) * t, 10)
    return round(min(snapped, 0.99), 2)


def _snap_down(price: float, tick: float) -> float:
    """Redondea HACIA ABAJO al tick (mín 0.01) — para ventas marketables que crucen el bid."""
    t = max(tick, 0.01)
    snapped = round(math.floor(round(price / t, 6)) * t, 10)
    return round(max(snapped, 0.01), 2)


def _calc_size(bet_usdc: float, price: float) -> float:
    """
    Shares enteros: con precio a 2 decimales, size entero garantiza maker limpio.
    Polymarket exige un valor mínimo de orden de ~$1: a precios altos floor()
    daba 1 share (p.ej. $0.81) → 400 'invalid amount' en bucle (pasó el 11-jun
    con Lyon: Trungelliti). Se sube al mínimo que garantice ≥$1.05 de valor.
    """
    if price <= 0:
        return 0
    size = math.floor(bet_usdc / price)
    min_size = math.ceil(1.05 / price)   # mínimo para superar el $1 de Polymarket
    return max(min_size, size, 1)


def _order_status(resp) -> str:
    if isinstance(resp, dict):
        return (resp.get("status") or "").lower()
    return (getattr(resp, "status", "") or "").lower()


# Estados que cuentan como "la orden se ejecutó (o se ejecutará ya)":
# - matched/filled/mined/success: fill inmediato confirmado
# - delayed: Polymarket acepta la orden marketable pero la cruza con unos
#   segundos de retraso. Tratarla como no-fill provocaba reintentos que
#   chocaban con "not enough balance" y cierres GHOST falsos (pasó el 11-jun
#   con Lyon y Udvardy: la 1ª venta 'delayed' SÍ se ejecutó).
_FILLED_STATUSES = ("matched", "filled", "mined", "success", "delayed")


def execute_signal(client: ClobClient, signal: Signal, market_question: str,
                   market: dict = None, paper: bool = False) -> bool:
    if signal.action == "HOLD":
        return False

    label = "[PAPER] " if paper else ("[DRY RUN] " if config.DRY_RUN else "")

    # ── Precio ejecutable real desde el libro CLOB ────────────────────────────
    # Se aplica también a paper: así el simulador entra al precio que de verdad
    # pagarías, no al outcomePrices retrasado (que generaba TPs falsos en segundos).
    bid, ask = get_best_bid_ask(signal.token_id)
    if ask is None:
        logger.info(f"{label}Sin asks en el libro — nadie vende, mercado sin liquidez: {market_question[:55]}")
        return False
    if bid is None:
        logger.info(f"{label}Sin bids en el libro — no habría salida para TP/SL: {market_question[:55]}")
        return False
    spread = ask - bid
    if spread > MAX_SPREAD:
        logger.warning(
            f"{label}Mercado ilíquido (spread={spread*100:.1f}¢ > {MAX_SPREAD*100:.0f}¢): "
            f"{market_question[:55]} — apuesta omitida."
        )
        return False
    if abs(ask - signal.price) > MAX_STALE_GAP:
        logger.warning(
            f"{label}Señal desfasada (señal={signal.price:.3f} vs ask real={ask:.3f}): "
            f"{market_question[:55]} — omitida. outcomePrices va retrasado respecto al CLOB."
        )
        return False

    tick = _get_tick(market)
    order_price = _snap_up(ask, tick)   # marketable: cruza el ask → fill inmediato

    # TP +7% debe ser alcanzable desde el precio REAL pagado (no el de la señal)
    MAX_ENTRY = 0.90
    if order_price > MAX_ENTRY:
        logger.info(f"{label}Descartado (precio ejecutable {order_price:.2f} > {MAX_ENTRY}, TP inalcanzable): {market_question[:55]}")
        return False

    # ── Sizing ────────────────────────────────────────────────────────────────
    real_balance = None
    if not paper and not config.DRY_RUN and client:
        try:
            from bot import get_real_balance
            real_balance = get_real_balance(client)
        except Exception:
            pass

    bet_usdc = (
        get_bet_size(market, paper=paper, price=order_price,
                     confidence=signal.confidence, balance=real_balance)
        if market else (config.PAPER_BET_USDC if paper else config.MAX_BET_USDC)
    )
    size = _calc_size(bet_usdc, order_price)

    logger.info(
        f"{label}COMPRA [{signal.strategy}]: {signal.action} | "
        f"Mercado: {market_question[:55]} | "
        f"Ask real: {order_price:.2f} (señal {signal.price:.3f}, spread {spread*100:.1f}¢) | "
        f"Tamaño: {size} shares | ${round(order_price * size, 2)} | {signal.reason}"
    )

    meta = dict(
        strategy=signal.strategy,
        sport=(market or {}).get("_sport") or "",
        confidence=round(signal.confidence, 3),
        signal_price=signal.price,
        spread_cents=round(spread * 100, 1),
        hours_to_start=(market or {}).get("_hours_to_start", 0.0),
    )

    if paper or config.DRY_RUN:
        add_position(signal.token_id, signal.action, order_price, size,
                     round(order_price * size, 2), market_question, paper=paper, **meta)
        return True

    # ── Verificar balance disponible ──────────────────────────────────────────
    try:
        from bot import get_real_balance
        available = get_real_balance(client)
        if available is not None and available < order_price * size:
            logger.warning(
                f"Balance insuficiente (${available:.2f} disponible, necesita ${order_price*size:.2f}): "
                f"{market_question[:55]} — apuesta omitida."
            )
            return False
    except Exception:
        pass

    # ── Orden FOK: o se llena YA al precio del ask, o no existe ───────────────
    try:
        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=signal.token_id, price=order_price, size=size, side=Side.BUY),
            options=_options(market) if market else None,
            order_type=OrderType.FOK,
        )
    except Exception as e:
        msg = str(e).lower()
        if any(kw in msg for kw in ("fok", "fill", "match", "killed")):
            logger.info(f"FOK no llenada (liquidez retirada al cruzar): {market_question[:55]} — sin posición.")
        else:
            logger.error(f"Error ejecutando compra: {e}")
        return False

    status = _order_status(resp)
    if status not in _FILLED_STATUSES:
        # FOK no llenada → el exchange la canceló: no hay orden viva ni posición
        logger.info(f"FOK no llenada (status={status or 'desconocido'}): {market_question[:55]} — sin posición.")
        return False

    logger.success(f"Compra ejecutada (status={status}) @ {order_price:.2f}: {resp}")
    add_position(signal.token_id, signal.action, order_price, size,
                 round(order_price * size, 2), market_question, paper=False, **meta)
    return True


def execute_sell(client: ClobClient, position: Position, current_price: float,
                 reason: str, market: dict = None, paper: bool = False) -> bool:
    pnl = (current_price - position.entry_price) * position.size
    pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
    label = "[PAPER] " if paper else ("[DRY RUN] " if config.DRY_RUN else "")

    # Etiqueta del resultado: trailing/tiempo se clasifican por el signo del P&L
    # (antes un trailing que cerraba con +4% quedaba registrado como "SL")
    if "TAKE PROFIT" in reason:
        result = "TP"
    elif "STOP LOSS" in reason:
        result = "SL"
    else:
        result = "TP" if pnl >= 0 else "SL"

    logger.info(
        f"{label}VENTA ({reason}): {position.action} | "
        f"Mercado: {position.market_question[:55]} | "
        f"Entrada: {position.entry_price:.3f} → Actual: {current_price:.3f} | "
        f"P&L: {pnl:+.2f} USDC ({pnl_pct:+.1f}%)"
    )

    if paper or config.DRY_RUN:
        remove_position(position.token_id, current_price, result, paper=paper)
        return True

    # ── Venta real: FOK cruzando el mejor bid ─────────────────────────────────
    bid, _ask = get_best_bid_ask(position.token_id)
    if bid is None:
        logger.warning(
            f"Sin bids para vender: {position.market_question[:45]} — reintento en el próximo ciclo. "
            f"(Si el mercado ya resolvió, redime la posición manualmente en Polymarket)"
        )
        return False

    tick = _get_tick(market)
    sell_price = _snap_down(bid, tick)
    sell_size = int(position.size)
    if sell_size <= 0:
        sell_size = _calc_size(position.usdc_spent, sell_price)

    try:
        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=position.token_id, price=sell_price,
                                 size=sell_size, side=Side.SELL),
            options=_options(market) if market else PartialCreateOrderOptions(tick_size="0.01"),
            order_type=OrderType.FOK,
        )
    except Exception as e:
        err_str = str(e)
        if "not enough balance" in err_str or "balance is not enough" in err_str:
            logger.warning(
                f"Posición fantasma detectada: {position.market_question[:55]} — eliminando del registro."
            )
            remove_position(position.token_id, current_price, "GHOST", paper=False)
        elif any(kw in err_str.lower() for kw in ("fok", "fill", "match", "killed")):
            logger.info(f"Venta FOK no llenada — reintento en el próximo ciclo: {position.market_question[:45]}")
        else:
            logger.error(f"Error ejecutando venta: {e}")
        return False

    status = _order_status(resp)
    if status not in _FILLED_STATUSES:
        logger.info(f"Venta FOK no llenada (status={status or 'desconocido'}) — reintento en el próximo ciclo.")
        return False

    # P&L real con el precio de venta REAL (el bid cruzado), no el midpoint
    real_pnl = (sell_price - position.entry_price) * position.size
    if "TAKE PROFIT" not in reason and "STOP LOSS" not in reason:
        result = "TP" if real_pnl >= 0 else "SL"
    logger.success(f"Venta ejecutada y llenada @ {sell_price:.2f}: {resp}")
    remove_position(position.token_id, sell_price, result, paper=False)
    return True

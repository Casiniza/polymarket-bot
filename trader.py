"""Ejecuta órdenes de compra y venta en Polymarket via py-clob-client-v2."""
import math
from loguru import logger
from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions, Side
from py_clob_client_v2.constants import POLYGON

import config
from strategy import Signal, get_bet_size
from positions import Position, add_position, remove_position


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


def _snap_price(price: float, tick: float) -> float:
    """
    Redondea el precio al tick del mercado, pero siempre con máx 2 decimales.
    Con tick=0.001 usamos 0.01 como mínimo porque Polymarket exige que
    maker_amount (price × size) tenga ≤ 2 decimales — imposible con 3.
    """
    effective_tick = max(tick, 0.01)   # nunca más fino que 0.01 para garantizar maker limpio
    snapped = round(round(price / effective_tick) * effective_tick, 10)
    return round(snapped, 2)


def _calc_size(bet_usdc: float, price: float) -> float:
    """
    Calcula size (shares enteros) tal que price × size tenga exactamente 2 decimales.
    Con price redondeado a 2 decimales, un size entero siempre produce maker limpio.
    """
    if price <= 0:
        return 0
    size = math.floor(bet_usdc / price)   # entero, garantiza maker = price(2dec) × int → 2dec
    return max(1, size)


def execute_signal(client: ClobClient, signal: Signal, market_question: str,
                   market: dict = None, paper: bool = False) -> bool:
    if signal.action == "HOLD":
        return False

    bet_usdc = get_bet_size(market, paper=paper, price=signal.price) if market else (config.PAPER_BET_USDC if paper else config.MAX_BET_USDC)

    tick = _get_tick(market)
    order_price = _snap_price(signal.price, tick)
    size = _calc_size(bet_usdc, order_price)

    label = "[PAPER] " if paper else ("[DRY RUN] " if config.DRY_RUN else "")

    logger.info(
        f"{label}COMPRA [{signal.strategy}]: {signal.action} | "
        f"Mercado: {market_question[:55]} | "
        f"Precio: {order_price:.4f} (tick {tick}) | Tamaño: {size} shares | "
        f"${round(order_price * size, 2)} | {signal.reason}"
    )

    if paper or config.DRY_RUN:
        add_position(signal.token_id, signal.action, order_price, size,
                     bet_usdc, market_question, paper=paper)
        return True

    # Verificar balance disponible antes de intentar la compra
    try:
        from bot import get_real_balance
        available = get_real_balance(client)
        if available is not None and available < bet_usdc:
            logger.warning(
                f"Balance insuficiente (${available:.2f} disponible, necesita ${bet_usdc:.2f}): "
                f"{market_question[:55]} — apuesta omitida."
            )
            return False
    except Exception:
        pass  # Si falla la consulta de balance, intentamos igualmente

    try:
        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=signal.token_id, price=order_price, size=size, side=Side.BUY),
            options=_options(market) if market else None,
            order_type=OrderType.FOK,
        )
        resp_str = str(resp)
        if "canceled" in resp_str.lower() or "cancelled" in resp_str.lower():
            logger.warning(f"Compra cancelada (sin liquidez al precio {order_price:.4f}): {market_question[:55]}")
            return False
        logger.success(f"Compra ejecutada: {resp}")
        add_position(signal.token_id, signal.action, order_price, size,
                     bet_usdc, market_question, paper=False)
        return True
    except Exception as e:
        logger.error(f"Error ejecutando compra: {e}")
        return False


def execute_sell(client: ClobClient, position: Position, current_price: float,
                 reason: str, market: dict = None, paper: bool = False) -> bool:
    pnl = (current_price - position.entry_price) * position.size
    pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
    label = "[PAPER] " if paper else ("[DRY RUN] " if config.DRY_RUN else "")
    result = "TP" if "TAKE PROFIT" in reason else "SL"

    logger.info(
        f"{label}VENTA ({reason}): {position.action} | "
        f"Mercado: {position.market_question[:55]} | "
        f"Entrada: {position.entry_price:.3f} → Actual: {current_price:.3f} | "
        f"P&L: {pnl:+.2f} USDC ({pnl_pct:+.1f}%)"
    )

    if paper or config.DRY_RUN:
        remove_position(position.token_id, current_price, result, paper=paper)
        return True

    try:
        tick = _get_tick(market)
        sell_price = _snap_price(min(max(current_price, 0.01), 0.99), tick)
        sell_size = _calc_size(position.usdc_spent, sell_price)

        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=position.token_id, price=sell_price,
                                 size=sell_size, side=Side.SELL),
            options=_options(market) if market else PartialCreateOrderOptions(tick_size="0.01"),
            order_type=OrderType.GTC,
        )
        logger.success(f"Venta ejecutada: {resp}")
        remove_position(position.token_id, current_price, result, paper=False)
        return True
    except Exception as e:
        err_str = str(e)
        if "not enough balance" in err_str or "balance is not enough" in err_str:
            logger.warning(
                f"Posición fantasma detectada: {position.market_question[:55]} — eliminando del registro."
            )
            remove_position(position.token_id, current_price, "GHOST", paper=False)
        else:
            logger.error(f"Error ejecutando venta: {e}")
        return False

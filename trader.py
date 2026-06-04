"""Ejecuta órdenes de compra y venta en Polymarket via py-clob-client."""
from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds, PartialCreateOrderOptions
from py_clob_client.constants import POLYGON

import config
from strategy import Signal
from positions import Position, add_position, remove_position


def build_client() -> ClobClient:
    client = ClobClient(
        host=config.CLOB_HOST,
        key=config.PRIVATE_KEY,
        chain_id=POLYGON,
        creds=ApiCreds(
            api_key=config.CLOB_API_KEY,
            api_secret=config.CLOB_API_SECRET,
            api_passphrase=config.CLOB_API_PASSPHRASE,
        ),
    )
    return client


def _order_options(market: dict) -> PartialCreateOrderOptions:
    """Extrae tick_size y neg_risk del mercado para firmar correctamente."""
    tick = str(market.get("orderPriceMinTickSize", "0.01"))
    # Normaliza a formato válido
    valid_ticks = {"0.1", "0.01", "0.001", "0.0001"}
    if tick not in valid_ticks:
        tick = "0.01"
    neg_risk = bool(market.get("negRisk", False))
    return PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk)


def execute_signal(client: ClobClient, signal: Signal, market_question: str, market: dict = None) -> bool:
    """Coloca una orden de compra y registra la posición."""
    if signal.action == "HOLD":
        return False

    size = round(config.MAX_BET_USDC / signal.price, 2) if signal.price > 0 else 0

    logger.info(
        f"{'[DRY RUN] ' if config.DRY_RUN else ''}COMPRA: {signal.action} | "
        f"Mercado: {market_question[:60]} | "
        f"Precio: {signal.price:.3f} | Tamaño: {size} shares | Razón: {signal.reason}"
    )

    if config.DRY_RUN:
        logger.info("DRY_RUN=true — orden simulada, posición registrada.")
        add_position(signal.token_id, signal.action, signal.price, size,
                     config.MAX_BET_USDC, market_question)
        return True

    try:
        order_args = OrderArgs(
            token_id=signal.token_id,
            price=signal.price,
            size=size,
            side="BUY",
        )
        options = _order_options(market) if market else None
        resp = client.create_and_post_order(order_args, options)
        logger.success(f"Compra ejecutada: {resp}")
        add_position(signal.token_id, signal.action, signal.price, size,
                     config.MAX_BET_USDC, market_question)
        return True
    except Exception as e:
        logger.error(f"Error ejecutando compra: {e}")
        return False


def execute_sell(client: ClobClient, position: Position, current_price: float, reason: str, market: dict = None) -> bool:
    """Cierra una posición vendiendo los shares."""
    pnl = (current_price - position.entry_price) * position.size
    pnl_pct = (current_price - position.entry_price) / position.entry_price * 100

    logger.info(
        f"{'[DRY RUN] ' if config.DRY_RUN else ''}VENTA ({reason}): {position.action} | "
        f"Mercado: {position.market_question[:60]} | "
        f"Entrada: {position.entry_price:.3f} → Actual: {current_price:.3f} | "
        f"P&L: {pnl:+.2f} USDC ({pnl_pct:+.1f}%)"
    )

    if config.DRY_RUN:
        logger.info("DRY_RUN=true — venta simulada, posición cerrada.")
        remove_position(position.token_id)
        return True

    try:
        order_args = OrderArgs(
            token_id=position.token_id,
            price=current_price,
            size=position.size,
            side="SELL",
        )
        options = _order_options(market) if market else None
        resp = client.create_and_post_order(order_args, options)
        logger.success(f"Venta ejecutada: {resp}")
        remove_position(position.token_id)
        return True
    except Exception as e:
        logger.error(f"Error ejecutando venta: {e}")
        return False

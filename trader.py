"""Ejecuta órdenes en Polymarket via py-clob-client."""
from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, Side
from py_clob_client.constants import POLYGON

import config
from strategy import Signal


def build_client() -> ClobClient:
    client = ClobClient(
        host=config.CLOB_HOST,
        key=config.PRIVATE_KEY,
        chain_id=POLYGON,
        creds={
            "apiKey": config.CLOB_API_KEY,
            "secret": config.CLOB_API_SECRET,
            "passphrase": config.CLOB_API_PASSPHRASE,
        },
    )
    return client


def execute_signal(client: ClobClient, signal: Signal, market_question: str) -> bool:
    """
    Coloca una orden de mercado para la señal dada.
    Devuelve True si la orden se ejecutó (o simuló) correctamente.
    """
    if signal.action == "HOLD":
        return False

    side = Side.BUY
    size = round(config.MAX_BET_USDC / signal.price, 2) if signal.price > 0 else 0

    logger.info(
        f"{'[DRY RUN] ' if config.DRY_RUN else ''}Orden: {signal.action} | "
        f"Mercado: {market_question[:60]} | "
        f"Precio: {signal.price:.3f} | Tamaño: {size} | Razón: {signal.reason}"
    )

    if config.DRY_RUN:
        logger.info("DRY_RUN=true — orden no enviada.")
        return True

    try:
        order_args = OrderArgs(
            token_id=signal.token_id,
            price=signal.price,
            size=size,
            side=side,
        )
        resp = client.create_and_post_order(order_args)
        logger.success(f"Orden ejecutada: {resp}")
        return True
    except Exception as e:
        logger.error(f"Error ejecutando orden: {e}")
        return False

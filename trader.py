"""Ejecuta órdenes de compra y venta en Polymarket via py-clob-client-v2."""
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


def _options(market: dict) -> PartialCreateOrderOptions:
    tick = str(market.get("orderPriceMinTickSize", "0.01"))
    if tick not in {"0.1", "0.01", "0.001", "0.0001"}:
        tick = "0.01"
    return PartialCreateOrderOptions(tick_size=tick)


def execute_signal(client: ClobClient, signal: Signal, market_question: str,
                   market: dict = None, paper: bool = False) -> bool:
    if signal.action == "HOLD":
        return False

    bet_usdc = get_bet_size(market, paper=paper, price=signal.price) if market else (config.PAPER_BET_USDC if paper else config.MAX_BET_USDC)
    size = round(bet_usdc / signal.price, 2) if signal.price > 0 else 0
    label = "[PAPER] " if paper else ("[DRY RUN] " if config.DRY_RUN else "")

    logger.info(
        f"{label}COMPRA [{signal.strategy}]: {signal.action} | "
        f"Mercado: {market_question[:55]} | "
        f"Precio: {signal.price:.3f} | Tamaño: {size} shares | ${bet_usdc} | {signal.reason}"
    )

    if paper or config.DRY_RUN:
        add_position(signal.token_id, signal.action, signal.price, size,
                     bet_usdc, market_question, paper=paper)
        return True

    try:
        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=signal.token_id, price=signal.price, size=size, side=Side.BUY),
            options=_options(market) if market else None,
            order_type=OrderType.GTC,
        )
        logger.success(f"Compra ejecutada: {resp}")
        add_position(signal.token_id, signal.action, signal.price, size,
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
        # Polymarket limita precio entre 0.01 y 0.99
        sell_price = min(max(round(current_price, 2), 0.01), 0.99)
        resp = client.create_and_post_order(
            order_args=OrderArgs(token_id=position.token_id, price=sell_price,
                                 size=position.size, side=Side.SELL),
            options=_options(market) if market else PartialCreateOrderOptions(tick_size="0.01"),
            order_type=OrderType.GTC,
        )
        logger.success(f"Venta ejecutada: {resp}")
        remove_position(position.token_id, current_price, result, paper=False)
        return True
    except Exception as e:
        logger.error(f"Error ejecutando venta: {e}")
        return False

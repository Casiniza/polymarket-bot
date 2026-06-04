"""
Polymarket Trading Bot
Ciclo principal: gestiona posiciones abiertas (TP/SL) y busca nuevas entradas.
"""
import sys
from loguru import logger
from markets import get_active_markets, get_prices_from_market, get_midpoint
from strategy import evaluate
from trader import build_client, execute_signal, execute_sell
from positions import load_positions
import config


logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="7 days", level="DEBUG")

TAKE_PROFIT = 0.10   # +10%
STOP_LOSS   = 0.10   # -10%


def check_positions(client):
    """Revisa posiciones abiertas y vende si se alcanza TP o SL."""
    positions = load_positions()
    if not positions:
        logger.info("Sin posiciones abiertas.")
        return

    logger.info(f"Revisando {len(positions)} posiciones abiertas...")
    for pos in positions:
        current_price = get_midpoint(pos.token_id)
        if current_price is None:
            logger.warning(f"No se pudo obtener precio para {pos.token_id[:12]}... — omitiendo")
            continue

        change = (current_price - pos.entry_price) / pos.entry_price

        if change >= TAKE_PROFIT:
            execute_sell(client, pos, current_price, f"TAKE PROFIT +{change*100:.1f}%")
        elif change <= -STOP_LOSS:
            execute_sell(client, pos, current_price, f"STOP LOSS {change*100:.1f}%")
        else:
            logger.info(
                f"Manteniendo: {pos.market_question[:55]} | "
                f"Entrada: {pos.entry_price:.3f} | Actual: {current_price:.3f} | "
                f"Cambio: {change*100:+.1f}%"
            )


def run_cycle(client, markets_limit: int = 30):
    logger.info(f"=== CICLO | Estrategia: {config.STRATEGY} | DRY_RUN: {config.DRY_RUN} ===")

    # 1. Primero gestiona posiciones existentes (TP/SL)
    check_positions(client)

    # 2. Busca nuevas oportunidades de entrada
    markets = get_active_markets(limit=markets_limit)
    logger.info(f"Mercados obtenidos: {len(markets)}")

    open_token_ids = {p.token_id for p in load_positions()}
    executed = 0

    for market in markets:
        question = market.get("question", "Sin título")
        tokens = market.get("tokens") or []

        # Usa outcomePrices directo (más rápido y fiable que llamar al CLOB)
        yes_price, no_price = get_prices_from_market(market)

        # Inyecta token_ids desde clobTokenIds para que la estrategia pueda usarlos
        clob_ids = market.get("clobTokenIds") or []
        if len(clob_ids) >= 2 and not market.get("tokens"):
            market["tokens"] = [
                {"outcome": "YES", "token_id": clob_ids[0]},
                {"outcome": "NO",  "token_id": clob_ids[1]},
            ]

        if yes_price is None:
            continue

        signal = evaluate(market, yes_price, no_price)

        # No entrar en un mercado donde ya tenemos posición
        if signal.action != "HOLD" and signal.token_id not in open_token_ids:
            ok = execute_signal(client, signal, question)
            if ok:
                executed += 1
                open_token_ids.add(signal.token_id)

    logger.info(f"=== FIN CICLO | Nuevas órdenes: {executed} | Mercados analizados: {len(markets)} ===")
    return executed


def main():
    if not config.PRIVATE_KEY and not config.DRY_RUN:
        logger.error("PRIVATE_KEY no configurada y DRY_RUN=false. Abortando.")
        sys.exit(1)

    client = build_client() if not config.DRY_RUN else None
    run_cycle(client, markets_limit=30)


if __name__ == "__main__":
    main()

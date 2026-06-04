"""
Polymarket Trading Bot
Punto de entrada principal. Escanea mercados activos y ejecuta señales.
"""
import sys
from loguru import logger
from markets import get_active_markets, get_midpoint
from strategy import evaluate
from trader import build_client, execute_signal
import config


logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="7 days", level="DEBUG")


def run_cycle(client, markets_limit: int = 30):
    logger.info(f"Iniciando ciclo | Estrategia: {config.STRATEGY} | DRY_RUN: {config.DRY_RUN}")

    markets = get_active_markets(limit=markets_limit)
    logger.info(f"Mercados obtenidos: {len(markets)}")

    executed = 0
    for market in markets:
        question = market.get("question", "Sin título")
        tokens = market.get("tokens") or []

        yes_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome", "").upper() == "YES"), None)
        no_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome", "").upper() == "NO"), None)

        yes_price = get_midpoint(yes_token["token_id"]) if yes_token else None
        no_price = get_midpoint(no_token["token_id"]) if no_token else None

        if yes_price is None:
            continue

        signal = evaluate(market, yes_price, no_price)

        if signal.action != "HOLD":
            ok = execute_signal(client, signal, question)
            if ok:
                executed += 1

    logger.info(f"Ciclo completado. Órdenes ejecutadas: {executed}/{len(markets)} mercados analizados")
    return executed


def main():
    if not config.PRIVATE_KEY and not config.DRY_RUN:
        logger.error("PRIVATE_KEY no configurada y DRY_RUN=false. Abortando.")
        sys.exit(1)

    client = build_client() if not config.DRY_RUN else None
    run_cycle(client, markets_limit=30)


if __name__ == "__main__":
    main()

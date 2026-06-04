"""
Polymarket Trading Bot — modo continuo
- Cada 30s: revisa posiciones abiertas (Take Profit / Stop Loss)
- Cada 5min: escanea nuevos mercados
- Solo apuesta en mercados que terminan hoy o mañana
- Nunca apuesta dos veces en el mismo mercado
"""
import sys
import time
from datetime import datetime, timezone, timedelta
from loguru import logger
from markets import get_active_markets, get_prices_from_market, get_midpoint
from strategy import evaluate
from trader import build_client, execute_signal, execute_sell
from positions import load_positions, save_positions
import config

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="7 days", level="DEBUG")

TAKE_PROFIT      = 0.10   # +10%
STOP_LOSS        = 0.10   # -10%
SCAN_POSITIONS_S = 30     # revisar posiciones cada 30 segundos
SCAN_MARKETS_S   = 300    # buscar mercados cada 5 minutos
MAX_RUNTIME_S    = 4 * 3600  # 4 horas — el cron lanza uno nuevo cada 4h para cobertura 24/7


WINNER_KEYWORDS = [
    " vs ", "vs.", "win the", "winner", "world cup", "champions league",
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    "super bowl", "playoffs", "finals", "semifinal", "quarterfinal",
    "nba finals", "ufc", "boxing",
]

# Mercados que NO son de ganador directo — los excluimos
NON_WINNER_KEYWORDS = [
    "over/under", "o/u", "spread", "total", "points", "goals",
    "score", "half", "quarter", "first", "last", "both teams",
    "clean sheet", "anytime", "assist", "card", "corner",
]

POLITICS_KEYWORDS = [
    "election", "elect", "president", "mayor", "senator", "governor",
    "congress", "parliament", "vote", "ballot", "candidate", "political",
    "minister", "chancellor", "prime minister", "poll", "polling",
    "democrat", "republican", "party", "campaign", "ceasefire",
    "peace deal", "treaty", "sanction", "tariff", "war",
    "iran", "russia", "ukraine", "israel", "gaza", "nato",
    "trump", "biden", "macron", "zelensky", "putin",
]

LIVE_KEYWORDS = ["live", "in-play", "in play", "currently", "right now"]


def is_winner_sports_market(market: dict) -> bool:
    """True solo si es un mercado de ganador directo de un partido deportivo."""
    question = market.get("question", "").lower()
    tags = [t.get("label", "").lower() for t in (market.get("tags") or []) if isinstance(t, dict)]
    category = (market.get("category") or "").lower()
    text = question + " " + category + " " + " ".join(tags)

    if any(kw in text for kw in POLITICS_KEYWORDS):
        return False
    if any(kw in text for kw in LIVE_KEYWORDS):
        return False
    if any(kw in text for kw in NON_WINNER_KEYWORDS):
        return False
    return any(kw in text for kw in WINNER_KEYWORDS)


def get_match_key(market: dict) -> str:
    """
    Clave única por partido — extrae los equipos/nombres del título.
    Evita apostar dos veces en el mismo partido.
    """
    q = market.get("question", "").lower()
    # Normaliza: quita texto después de "?" o ":" para quedarse con el nombre base
    q = q.split("?")[0].split(":")[0].strip()
    return q


def market_ends_by_tomorrow(market: dict) -> bool:
    """True si el mercado termina en los próximos 2 días."""
    end_str = market.get("endDateIso") or market.get("endDate", "")
    if not end_str:
        return False
    try:
        end_date = datetime.fromisoformat(end_str[:10])
        cutoff = (datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None)
        return end_date.date() <= cutoff.date()
    except (ValueError, TypeError):
        return False


def get_market_id(market: dict) -> str:
    """Identificador único del mercado (conditionId o id)."""
    return market.get("conditionId") or market.get("id", "")


def check_positions(client):
    """Revisa posiciones abiertas y ejecuta TP/SL si corresponde."""
    positions = load_positions()
    if not positions:
        return

    logger.info(f"Revisando {len(positions)} posiciones...")
    for pos in positions:
        current_price = get_midpoint(pos.token_id)
        if current_price is None:
            logger.warning(f"Sin precio para {pos.token_id[:12]}...")
            continue

        change = (current_price - pos.entry_price) / pos.entry_price

        if change >= TAKE_PROFIT:
            execute_sell(client, pos, current_price, f"TAKE PROFIT +{change*100:.1f}%")
        elif change <= -STOP_LOSS:
            execute_sell(client, pos, current_price, f"STOP LOSS {change*100:.1f}%")
        else:
            logger.info(
                f"Manteniendo | {pos.market_question[:50]} | "
                f"{pos.entry_price:.3f} → {current_price:.3f} ({change*100:+.1f}%)"
            )


def scan_markets(client, bet_market_ids: set, bet_match_keys: set) -> tuple[set, set]:
    """
    Busca nuevas oportunidades de ganador directo.
    Devuelve sets actualizados de market_ids y match_keys apostados.
    """
    markets = get_active_markets(limit=100)
    open_token_ids = {p.token_id for p in load_positions()}
    new_bets = 0

    for market in markets:
        if not market_ends_by_tomorrow(market):
            continue
        if not is_winner_sports_market(market):
            continue

        market_id = get_market_id(market)
        if market_id in bet_market_ids:
            continue

        match_key = get_match_key(market)
        if match_key in bet_match_keys:
            logger.debug(f"Ya apostado en este partido: {match_key[:55]}")
            continue

        yes_price, no_price = get_prices_from_market(market)
        if yes_price is None:
            continue

        clob_ids = market.get("clobTokenIds") or []
        if len(clob_ids) >= 2 and not market.get("tokens"):
            market["tokens"] = [
                {"outcome": "YES", "token_id": clob_ids[0]},
                {"outcome": "NO",  "token_id": clob_ids[1]},
            ]

        signal = evaluate(market, yes_price, no_price)

        if signal.action != "HOLD" and signal.token_id not in open_token_ids:
            question = market.get("question", "")
            ok = execute_signal(client, signal, question)
            if ok:
                bet_market_ids.add(market_id)
                bet_match_keys.add(match_key)
                open_token_ids.add(signal.token_id)
                new_bets += 1

    winner_markets = sum(1 for m in markets if market_ends_by_tomorrow(m) and is_winner_sports_market(m))
    logger.info(f"Scan completado | {winner_markets} partidos (ganador) elegibles | {new_bets} nuevas apuestas")
    return bet_market_ids, bet_match_keys


def main():
    if not config.PRIVATE_KEY and not config.DRY_RUN:
        logger.error("PRIVATE_KEY no configurada y DRY_RUN=false. Abortando.")
        sys.exit(1)

    client = build_client() if not config.DRY_RUN else None

    logger.info(f"Bot iniciado | TP: +{TAKE_PROFIT*100:.0f}% | SL: -{STOP_LOSS*100:.0f}% | "
                f"Scan posiciones: {SCAN_POSITIONS_S}s | Scan mercados: {SCAN_MARKETS_S}s")

    existing_positions = load_positions()
    bet_market_ids: set = {p.token_id for p in existing_positions}
    bet_match_keys: set = {get_match_key({"question": p.market_question}) for p in existing_positions}
    start_time = time.time()
    last_market_scan = 0  # fuerza scan inmediato al arrancar

    while time.time() - start_time < MAX_RUNTIME_S:
        now = time.time()

        # Scan de mercados cada 5 minutos
        if now - last_market_scan >= SCAN_MARKETS_S:
            logger.info("=== SCAN MERCADOS ===")
            bet_market_ids, bet_match_keys = scan_markets(client, bet_market_ids, bet_match_keys)
            last_market_scan = now

        # Revisión de posiciones cada 30 segundos
        check_positions(client)

        elapsed = int(time.time() - start_time)
        logger.debug(f"Tiempo transcurrido: {elapsed//3600}h {(elapsed%3600)//60}m | Próximo scan mercados en {max(0, int(SCAN_MARKETS_S-(time.time()-last_market_scan)))}s")
        time.sleep(SCAN_POSITIONS_S)

    logger.info("Bot detenido — límite de tiempo alcanzado.")


if __name__ == "__main__":
    main()

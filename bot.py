"""
Polymarket Trading Bot — modo continuo
- TP/SL: revisión continua cada 3 segundos
- Cada 5min: escanea nuevos mercados
- Solo apuesta en mercados que terminan en los próximos 7 días
- Nunca apuesta dos veces en el mismo mercado
- Servidor HTTP local en puerto 7373 para logs en vivo en el dashboard
"""
import sys
import time
import threading
import collections
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from loguru import logger
from markets import get_active_markets, get_prices_from_market, get_midpoint
from strategy import evaluate
from trader import build_client, execute_signal, execute_sell
from positions import load_positions, save_positions, load_history
import config

TAKE_PROFIT      = 0.10
STOP_LOSS        = 0.10
SCAN_POSITIONS_S = 3      # TP/SL cada 3 segundos — continuo
SCAN_MARKETS_S   = 300    # buscar mercados cada 5 minutos
MAX_RUNTIME_S    = 4 * 3600
LOG_PORT         = 7373
MAX_LOG_LINES    = 200

# Buffer circular de logs para el dashboard
_log_buffer: collections.deque = collections.deque(maxlen=MAX_LOG_LINES)

def _sink(message):
    """Captura cada línea de log y la guarda en el buffer."""
    _log_buffer.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": message.record["level"].name,
        "text": message.record["message"],
    })

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="7 days", level="DEBUG")
logger.add(_sink, level="INFO", format="{message}")


class LogHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(list(_log_buffer)).encode())

    def log_message(self, *args):
        pass  # silencia los logs del servidor HTTP


def start_log_server():
    """Arranca el servidor de logs en un hilo separado."""
    try:
        server = HTTPServer(("localhost", LOG_PORT), LogHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"Servidor de logs activo en http://localhost:{LOG_PORT}")
    except Exception as e:
        logger.warning(f"No se pudo arrancar el servidor de logs: {e}")


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


def get_daily_loss() -> float:
    """Calcula la pérdida realizada de hoy (negativo = pérdida)."""
    today = datetime.now(timezone.utc).date()
    history = load_history()
    daily_pnl = sum(
        h.get("pnl", 0) for h in history
        if h.get("pnl", 0) < 0 and
        datetime.fromisoformat(h.get("closed_at", "2000-01-01")).date() == today
    )
    return abs(daily_pnl)


def daily_loss_exceeded() -> bool:
    """True si ya se alcanzó el límite de pérdida diaria."""
    loss = get_daily_loss()
    if loss >= config.MAX_DAILY_LOSS_USDC:
        logger.warning(
            f"LÍMITE DE PÉRDIDA DIARIA alcanzado: -${loss:.2f} / -${config.MAX_DAILY_LOSS_USDC:.2f}. "
            f"No se abrirán nuevas apuestas hoy."
        )
        return True
    return False


def has_enough_liquidity(market: dict) -> bool:
    """True si el mercado tiene volumen 24h suficiente."""
    vol = float(market.get("volume24hr") or market.get("volume24hrClob") or 0)
    if vol < config.MIN_MARKET_VOLUME:
        logger.debug(f"Descartado (volumen ${vol:.0f} < ${config.MIN_MARKET_VOLUME:.0f}): {market.get('question','')[:50]}")
        return False
    return True


def market_ends_by_tomorrow(market: dict) -> bool:
    """True si el mercado termina en los próximos 7 días."""
    end_str = market.get("endDateIso") or market.get("endDate", "")
    if not end_str:
        return False
    try:
        end_date = datetime.fromisoformat(end_str[:10])
        cutoff = (datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None)
        return end_date.date() <= cutoff.date()
    except (ValueError, TypeError):
        return False


def market_not_started(market: dict) -> bool:
    """
    True si el partido AÚN NO ha empezado.
    Usa el startDate del mercado — si es en el futuro, el partido no ha comenzado.
    Si no hay startDate o es ambiguo, permite la apuesta (conservador).
    """
    start_str = market.get("startDate") or market.get("startDateIso") or ""
    if not start_str:
        return True
    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        # El partido no ha empezado si faltan más de 5 minutos para el inicio
        return start_dt > now + timedelta(minutes=5)
    except (ValueError, TypeError):
        return True


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
    # Comprueba límite de pérdida diaria antes de buscar nuevas apuestas
    if daily_loss_exceeded():
        logger.info("Scan omitido — límite de pérdida diaria alcanzado.")
        return bet_market_ids, bet_match_keys

    markets = get_active_markets(limit=100)
    open_token_ids = {p.token_id for p in load_positions()}
    new_bets = 0

    for market in markets:
        if not market_ends_by_tomorrow(market):
            continue
        if not is_winner_sports_market(market):
            continue
        if not market_not_started(market):
            logger.debug(f"Descartado (partido en curso): {market.get('question','')[:60]}")
            continue
        if not has_enough_liquidity(market):
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
            ok = execute_signal(client, signal, question, market)
            if ok:
                bet_market_ids.add(market_id)
                bet_match_keys.add(match_key)
                open_token_ids.add(signal.token_id)
                new_bets += 1

    winner_markets = sum(1 for m in markets if market_ends_by_tomorrow(m) and is_winner_sports_market(m))
    daily_loss = get_daily_loss()
    logger.info(
        f"Scan completado | {winner_markets} partidos elegibles | {new_bets} nuevas apuestas | "
        f"Pérdida diaria: -${daily_loss:.2f} / -${config.MAX_DAILY_LOSS_USDC:.2f}"
    )
    return bet_market_ids, bet_match_keys


def main():
    if not config.PRIVATE_KEY and not config.DRY_RUN:
        logger.error("PRIVATE_KEY no configurada y DRY_RUN=false. Abortando.")
        sys.exit(1)

    start_log_server()
    client = build_client() if not config.DRY_RUN else None

    logger.info(f"Bot iniciado | TP: +{TAKE_PROFIT*100:.0f}% | SL: -{STOP_LOSS*100:.0f}% | "
                f"TP/SL continuo cada {SCAN_POSITIONS_S}s | Scan mercados cada {SCAN_MARKETS_S}s")

    existing_positions = load_positions()
    bet_market_ids: set = {p.token_id for p in existing_positions}
    bet_match_keys: set = {get_match_key({"question": p.market_question}) for p in existing_positions}
    start_time = time.time()
    last_market_scan = 0

    while time.time() - start_time < MAX_RUNTIME_S:
        now = time.time()

        # Scan de mercados cada 5 minutos
        if now - last_market_scan >= SCAN_MARKETS_S:
            logger.info("=== SCAN MERCADOS ===")
            bet_market_ids, bet_match_keys = scan_markets(client, bet_market_ids, bet_match_keys)
            last_market_scan = now

        # TP/SL continuo cada 3 segundos
        check_positions(client)
        time.sleep(SCAN_POSITIONS_S)

    logger.info("Bot detenido — límite de tiempo alcanzado.")


if __name__ == "__main__":
    main()

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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from loguru import logger
from markets import get_active_markets, get_prices_from_market, get_midpoint
from strategy import evaluate, is_world_cup
from trader import build_client, execute_signal, execute_sell
from positions import load_positions, save_positions, load_history
import config

# ── Parámetros de salida ─────────────────────────────────────────────────────
# Asimétrico: TP más fácil de alcanzar, SL da más margen para recuperarse.
# Break-even con SL/(TP+SL) = 8/(7+8) = 53.3% win rate — mucho más alcanzable que 50%
TAKE_PROFIT      = 0.07   # +7% TP — objetivos pequeños pero frecuentes
STOP_LOSS        = 0.08   # -8% SL asimétrico — más margen para rebotes
TRAILING_START   = 0.04   # trailing activo cuando la ganancia toca +4%
TRAILING_STOP    = 0.025  # vende si cae 2.5% desde el pico (más agresivo que antes)
MAX_HOLD_HOURS   = 20     # salida forzada base si la posición lleva >20h abierta
GAME_OVER_BUFFER_H = 6     # margen tras el inicio del partido antes de cerrar por tiempo
                          # (cubre prórroga/penaltis y partidos de tenis a 5 sets)
MAX_CONCURRENT   = 2      # máximo 2 posiciones reales simultáneas — calidad > cantidad
MIN_HOURS_ENTRY  = 1.5    # no entrar si el mercado cierra en < 1.5h (permite 1er cuarto/entrada)
# ── Parámetros de ciclo ───────────────────────────────────────────────────────
SCAN_POSITIONS_S = 3      # TP/SL cada 3 segundos — continuo
SCAN_MARKETS_S   = 300    # buscar mercados cada 5 minutos
LOG_PORT         = 7373
MAX_LOG_LINES    = 200

# Seguimiento del precio pico por posición — para trailing stop
_peak_prices: dict = {}   # {token_id: precio_pico}

# Fecha de cierre de mercado por token_id — para TP/SL adaptativo
_market_end_dates: dict = {}  # {token_id: "2026-06-08T20:00:00Z"}

# Ventana máxima de entrada — no apostar en mercados que cierran en >MAX_ENTRY_WINDOW_H horas
MAX_ENTRY_WINDOW_H = 36.0  # sweet spot: 2.5h–36h antes del cierre

# Cooldown de re-entrada — evita volver a apostar el mismo mercado tras un TP/SL
# Persiste en disco para sobrevivir reinicios del bot
REENTRY_COOLDOWN_H = 4.0   # 4h sin re-entrar tras cierre
_closed_cooldown: dict = {} # {match_key: "2026-...iso..."} — cargado al arrancar

# Watchdog anti-congelación: si el loop principal no da señales de vida en
# WATCHDOG_MAX_S, el proceso se auto-mata y el .bat lo relanza limpio.
# (El 10-jun una conexión HTTP sin timeout congeló el loop 21+ minutos.)
WATCHDOG_MAX_S = 300       # 5 min sin tick = congelado (un scan normal tarda <60s)
_last_tick: dict = {"t": 0.0}

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


_balance_cache: dict = {"usdc": None, "updated": 0}

def get_real_balance(client) -> float | None:
    """Obtiene el balance USDC real de Polymarket (cachea 60s)."""
    now = time.time()
    if _balance_cache["usdc"] is not None and now - _balance_cache["updated"] < 60:
        return _balance_cache["usdc"]
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        usdc = int(bal.get("balance", 0)) / 1e6
        _balance_cache["usdc"] = usdc
        _balance_cache["updated"] = now
        return usdc
    except Exception:
        return _balance_cache.get("usdc")


def _load_closed_cooldown():
    """Carga el cooldown de re-entrada desde disco al arrancar y filtra los expirados."""
    global _closed_cooldown
    try:
        with open("closed_cooldown.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=REENTRY_COOLDOWN_H)
        _closed_cooldown = {}
        for k, v in raw.items():
            try:
                dt = datetime.fromisoformat(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > cutoff:
                    _closed_cooldown[k] = v
            except Exception:
                pass
        logger.debug(f"Cooldown cargado: {len(_closed_cooldown)} mercados en cooldown")
    except Exception:
        _closed_cooldown = {}


def _mark_closed(match_key: str):
    """Marca un mercado como recientemente cerrado y lo persiste a disco."""
    _closed_cooldown[match_key] = datetime.now(timezone.utc).isoformat()
    try:
        with open("closed_cooldown.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump(_closed_cooldown, f, indent=2)
    except Exception:
        pass


def _is_in_cooldown(match_key: str) -> bool:
    """True si el mercado cerró hace menos de REENTRY_COOLDOWN_H horas."""
    ts = _closed_cooldown.get(match_key)
    if not ts:
        return False
    try:
        closed_dt = datetime.fromisoformat(ts)
        if closed_dt.tzinfo is None:
            closed_dt = closed_dt.replace(tzinfo=timezone.utc)
        hours_ago = (datetime.now(timezone.utc) - closed_dt).total_seconds() / 3600
        return hours_ago < REENTRY_COOLDOWN_H
    except Exception:
        return False


def _cooldown_key(market_question: str, paper: bool) -> str:
    """
    Clave de cooldown separada por modo: un SL en paper no debe bloquear
    la entrada del bot real en ese mercado (y viceversa).
    """
    prefix = "paper:" if paper else "real:"
    return prefix + get_match_key({"question": market_question})


# Errores de "el cliente colgó la conexión" — totalmente benignos:
# el dashboard aborta peticiones lentas (timeout 4s) o recarga la página.
_CLIENT_DISCONNECT = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class LogHandler(BaseHTTPRequestHandler):
    """Servidor HTTP local — el dashboard lo usa para datos en tiempo real."""
    _FILE_MAP = {
        "/positions":       "positions.json",
        "/paper_positions": "paper_positions.json",
        "/history":         "history.json",
        "/paper_history":   "paper_history.json",
        "/heartbeat":       "heartbeat.json",
    }
    _EMPTY = {
        "/positions": "[]", "/paper_positions": "[]",
        "/history": "[]",   "/paper_history": "[]",
        "/heartbeat": "{}",
    }

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            if path == "/balance":
                bal = _balance_cache.get("usdc")
                self.wfile.write(json.dumps({"usdc": bal}).encode())
            elif path in self._FILE_MAP:
                fname = self._FILE_MAP[path]
                try:
                    with open(fname, "r", encoding="utf-8-sig") as f:
                        self.wfile.write(f.read().encode("utf-8"))
                except OSError:
                    self.wfile.write(self._EMPTY.get(path, "{}").encode())
            else:  # /logs o cualquier otra ruta
                self.wfile.write(json.dumps(list(_log_buffer)).encode())
        except _CLIENT_DISCONNECT:
            pass  # el navegador cortó a mitad de respuesta — sin traceback

    def log_message(self, *args):
        pass


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """
    Threading: cada petición del dashboard va en su hilo — ya no se encolan
    detrás de una lenta (causa de los abortos a 4s del dashboard).
    handle_error silencia los tracebacks de desconexión del cliente.
    """
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, _CLIENT_DISCONNECT):
            return  # cliente desconectó — irrelevante, sin spam en la terminal
        super().handle_error(request, client_address)


def start_log_server():
    """Arranca el servidor de logs en un hilo separado."""
    try:
        server = _QuietThreadingHTTPServer(("localhost", LOG_PORT), LogHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"Servidor de logs activo en http://localhost:{LOG_PORT}")
    except Exception as e:
        logger.warning(f"No se pudo arrancar el servidor de logs: {e}")


def start_watchdog():
    """
    Hilo vigilante: si el loop principal lleva >WATCHDOG_MAX_S sin dar un tick
    (colgado en una llamada bloqueante), mata el proceso entero. El .bat lo
    relanza en 10s con estado limpio. Última red de seguridad para correr 24/7.
    """
    import os
    def _watch():
        while True:
            time.sleep(60)
            last = _last_tick["t"]
            if last and time.time() - last > WATCHDOG_MAX_S:
                logger.critical(
                    f"WATCHDOG: loop congelado {time.time()-last:.0f}s (> {WATCHDOG_MAX_S}s) "
                    f"— matando proceso para reinicio limpio."
                )
                os._exit(3)  # el .bat ve código ≠2 y relanza
    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    logger.info(f"Watchdog activo — reinicio automático si el loop se congela >{WATCHDOG_MAX_S//60} min")


# ── KEYWORDS DE FILTRADO ─────────────────────────────────────────────────────
# Solo mercados "X vs Y" — partido único, resultado claro en horas
MATCH_VS_KEYWORDS = [" vs ", "vs.", " vs\t"]

# Mercados de torneo/campeonato — EXCLUIDOS para real money
# "Will X win the [tournament]?" → semanas de incertidumbre, cambia con cada ronda
TOURNAMENT_KEYWORDS = [
    "win the ", "win the\t", "world cup", "champions league",
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    "super bowl", "stanley cup", "world series", "championship",
    "open winner", "grand prix winner", "grand slam",
    "will win the", "will be the",
]

# Mercados que NO son de ganador directo — los excluimos
NON_WINNER_KEYWORDS = [
    "over/under", "o/u", "spread", "total", "points", "goals",
    "score", "half", "quarter", "first", "last", "both teams",
    "clean sheet", "anytime", "assist", "card", "corner",
    "game 1", "game 2", "game 3", "map 1", "map 2",  # series individuales
    # Props/novedades que NO son resultado de partido — colaban por keyword de
    # deporte ("ufc" dentro de "UFC Freedom 250"): "Will Messi attend UFC 250?"
    # generó una señal real BUY_NO (la salvó el filtro de spread, libro 63¢).
    "attend", "show up", "appear", "be sacked", "be fired", "sign for",
    "transfer", "mvp", "ballon", "award", "ceremony",
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

# Mercados de crypto — demasiado volátiles para TP/SL de 7-8%
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "xrp", "doge", "dogecoin", "bnb", "price of", "above $", "below $",
    "up or down", "coin", "token", "defi", "nft",
]

# ── ESPORTS — PROHIBIDO para dinero real ─────────────────────────────────────
# Se resuelven en 30-45 minutos, precios caen en vertical al primer kill
ESPORTS_KEYWORDS = [
    "dota", "league of legends", "lol:", "counter-strike", "cs:", "csgo", "csg",
    "valorant", "overwatch", "starcraft", "hearthstone", "rocket league",
    "fortnite", "pubg", "apex legends", "esport", "e-sport",
    "lcs ", "lec ", "lcq ", "lpl ", "lck ", "worlds ", " msi ",
    "iem ", "esl ", "blast ", "pgl ", "dreamhack",
    "bo1)", "bo3)", "bo5)", "(bo1", "(bo3", "(bo5",
    "cloud9", "fnatic", "navi", "g2 esports", "faze clan", "team liquid esports",
    "game winner", "game 2 winner", "game 3 winner",
    # Mercados de handicap de mapas (CS2, Dota, etc.) — precio cae en vertical al resolverse
    "map handicap", "map 1 winner", "map 2 winner", "map 3 winner",
    # Equipos esports comunes no cubiertos antes
    "tyloo", "natus vincere", "astralis", "team vitality", "heroic",
    "eternal fire", "spirit", "big clan", "mouz", "complexity",
    "round winner", "pistol round", "knife round",
]

LIVE_KEYWORDS = ["live", "in-play", "in play", "currently", "right now"]

# ── Tabla de ajuste de confianza por deporte ─────────────────────────────────
# Basada en estadísticas históricas de win rate de favoritos por categoría
SPORT_CONF_BOOST: dict[str, float] = {
    # Deportes donde los favoritos son muy fiables → boost positivo
    "tennis":   +0.07,  # ATP/WTA top players ganan ~80-85% vs wildcards
    "nba":      +0.04,  # NBA favoritos ganan ~62% de partidos
    "wnba":     +0.04,
    "soccer":   +0.02,  # Fútbol: favoritos ganan ~55-60%
    # Deportes con alta varianza → penalización
    "mlb":      -0.02,  # Béisbol: alta varianza (55% favoritos)
    "nfl":      -0.03,  # NFL: alta varianza, cualquier equipo puede ganar
    "nhl":      -0.02,  # Hockey: similar a fútbol pero más varianza
    "ufc":      -0.06,  # MMA: altísima varianza, knock-outs inesperados
    "mma":      -0.06,
    "boxing":   -0.05,
}

def _detect_sport(market: dict) -> str | None:
    """Detecta el deporte del mercado desde el título y categoría."""
    q = (market.get("question") or "").lower()
    cat = (market.get("category") or "").lower()
    tags = " ".join(t.get("label","").lower() for t in (market.get("tags") or []) if isinstance(t,dict))
    text = q + " " + cat + " " + tags
    if any(kw in text for kw in ["tennis","atp","wta","roland garros","wimbledon","us open","australian open","french open","birmingham","eastbourne","queen's",
                                 "open:","championships:","lyon:","libema","stuttgart"]):
        return "tennis"
    if any(kw in text for kw in ["nba","basketball","lakers","celtics","warriors","bulls","heat","knicks","76ers","bucks","spurs"]):
        return "nba"
    if any(kw in text for kw in ["wnba","valkyries","aces","dream","mystics","fever","sky","liberty"]):
        return "wnba"
    if any(kw in text for kw in ["mlb","baseball","yankees","red sox","dodgers","cubs","mets","braves","astros","cardinals","giants","phillies","pirates","nationals","guardians","rangers","angels","padres","mariners","twins","rays","orioles"]):
        return "mlb"
    if any(kw in text for kw in ["nfl","american football","super bowl","patriots","chiefs","cowboys","eagles","49ers","packers","bills","rams","bengals"]):
        return "nfl"
    if any(kw in text for kw in ["nhl","hockey","maple leafs","bruins","penguins","blackhawks","rangers","capitals","oilers"]):
        return "nhl"
    # "ko" eliminado: matcheaba como substring dentro de "KOrea" (fútbol → ufc)
    if any(kw in text for kw in ["ufc","mma","bellator","pfl","fight night","submission"]):
        return "ufc"
    if any(kw in text for kw in ["boxing","bout","heavyweight","middleweight"]):
        return "boxing"
    if any(kw in text for kw in ["soccer","football","premier league","la liga","bundesliga","serie a","ligue 1","champions","europa league","mls","copa"]):
        return "soccer"
    return None


def is_esports_market(market: dict) -> bool:
    """True si el mercado es de esports — prohibido para dinero real."""
    question = (market.get("question") or "").lower()
    category = (market.get("category") or "").lower()
    text = question + " " + category
    return any(kw in text for kw in ESPORTS_KEYWORDS)


def is_tournament_winner_market(market: dict) -> bool:
    """
    True si el mercado es 'Will X win the [tournament]?' en vez de un partido directo.
    Estos mercados tienen semanas de incertidumbre — muchos más factores que un partido.
    Solo permitimos mercados de partido único: 'X vs Y'.
    """
    question = (market.get("question") or "").lower()
    # Si tiene "vs" es partido directo — OK
    if any(kw in question for kw in MATCH_VS_KEYWORDS):
        return False  # es partido directo, no torneo
    # Si contiene keywords de torneo → es torneo → excluir
    return any(kw in question for kw in TOURNAMENT_KEYWORDS)


def is_crypto_market(market: dict) -> bool:
    """True si el mercado es de crypto — los excluimos siempre."""
    question = market.get("question", "").lower()
    category = (market.get("category") or "").lower()
    text = question + " " + category
    return any(kw in text for kw in CRYPTO_KEYWORDS)


def is_draw_market(market: dict) -> bool:
    """
    True si es un mercado '¿X vs Y acabará en empate?' (fútbol).
    Comprar el NO aquí es nuestra MAYOR ventaja demostrada (3/4 ganados, mejores
    P&L): el empate es un resultado minoritario (~20-25%) y el público minorista
    lo sobrecompra como apuesta 'segura', dejando el NO infravalorado.
    """
    q = (market.get("question") or "").lower()
    return ("draw" in q or "empate" in q) and any(kw in q for kw in MATCH_VS_KEYWORDS)


def is_winner_sports_market(market: dict) -> bool:
    """True solo si es un mercado de ganador de partido deportivo (no esports, no torneo)."""
    question = market.get("question", "").lower()
    tags = [t.get("label", "").lower() for t in (market.get("tags") or []) if isinstance(t, dict)]
    category = (market.get("category") or "").lower()
    text = question + " " + category + " " + " ".join(tags)

    if any(kw in text for kw in POLITICS_KEYWORDS):
        return False
    if any(kw in text for kw in CRYPTO_KEYWORDS):
        return False
    if any(kw in text for kw in LIVE_KEYWORDS):
        return False
    if any(kw in text for kw in NON_WINNER_KEYWORDS):
        return False
    if any(kw in text for kw in ESPORTS_KEYWORDS):
        return False   # esports filtrado aquí también
    # Necesita "vs" para ser partido directo, o keywords deportivos específicos
    has_vs = any(kw in text for kw in MATCH_VS_KEYWORDS)
    has_sport = any(kw in text for kw in [
        "ufc", "boxing", "nba finals", "super bowl",  # excepciones sin "vs"
        "playoffs", "championship game",
    ])
    return has_vs or has_sport


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
    """Calcula la pérdida realizada de hoy. Excluye GHOSTs (nunca fueron apuestas reales)."""
    today = datetime.now(timezone.utc).date()
    history = load_history()
    daily_pnl = sum(
        h.get("pnl", 0) for h in history
        if h.get("pnl", 0) < 0
        and h.get("result") != "GHOST"
        and datetime.fromisoformat(h.get("closed_at", "2000-01-01")).date() == today
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


def get_weekly_loss() -> float:
    """Pérdida realizada desde el lunes de esta semana (excluye GHOSTs)."""
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())  # lunes
    history = load_history()
    weekly_pnl = sum(
        h.get("pnl", 0) for h in history
        if h.get("pnl", 0) < 0
        and h.get("result") != "GHOST"
        and datetime.fromisoformat(h.get("closed_at", "2000-01-01")).date() >= week_start
    )
    return abs(weekly_pnl)


def weekly_loss_exceeded() -> bool:
    """True si ya se alcanzó el límite de pérdida semanal."""
    loss = get_weekly_loss()
    if loss >= config.MAX_WEEKLY_LOSS_USDC:
        logger.warning(
            f"⛔ LÍMITE DE PÉRDIDA SEMANAL alcanzado: -${loss:.2f} / -${config.MAX_WEEKLY_LOSS_USDC:.2f}. "
            f"No se abrirán apuestas reales hasta la próxima semana."
        )
        return True
    return False


def market_is_mature(market: dict) -> bool:
    """
    True si el mercado tiene al menos MIN_MARKET_AGE_MIN minutos de vida.
    Los mercados recién creados tienen precios iniciales arbitrarios que se asientan
    con el primer volumen real — entrar demasiado pronto es apostar contra el creador.
    """
    created_str = market.get("createdAt") or market.get("created_at") or ""
    if not created_str:
        return True  # sin fecha de creación → asumir maduro (conservador)
    try:
        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
        if age_min < config.MIN_MARKET_AGE_MIN:
            logger.debug(
                f"Descartado (mercado nuevo, {age_min:.0f}min < {config.MIN_MARKET_AGE_MIN}min): "
                f"{market.get('question','')[:50]}"
            )
            return False
        return True
    except (ValueError, TypeError):
        return True


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


def get_game_start(market: dict) -> datetime | None:
    """
    Hora de inicio REAL del partido desde gameStartTime.
    OJO con los campos de fecha de Gamma (verificado contra la API 10-jun):
    - startDate  = fecha de LISTADO del mercado (≈createdAt) — NO sirve
    - endDate    = en MLB es el deadline de resolución (¡+7 días!) — NO es fin del juego
    - gameStartTime = inicio real del partido — formato "2026-06-10 17:10:00+00"
    """
    raw = market.get("gameStartTime") or ""
    if not raw:
        return None
    try:
        s = raw.strip().replace(" ", "T")
        # Normaliza zona "+00" → "+00:00" (fromisoformat de versiones viejas no la acepta)
        if len(s) >= 3 and (s[-3] == "+" or s[-3] == "-") and ":" not in s[-3:]:
            s = s + ":00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def game_entry_window_ok(market: dict) -> bool:
    """
    Ventana de entrada pre-partido basada en gameStartTime:
    - Partido empezó hace >10 min → EN JUEGO → fuera (swings matan el SL)
    - Falta más de MAX_ENTRY_WINDOW_H para el partido → demasiado pronto
    - Sin gameStartTime → se aplica el fallback con endDate en scan_markets
    """
    gs = get_game_start(market)
    if gs is None:
        return True  # sin dato → decide el fallback de endDate
    mins_to_start = (gs - datetime.now(timezone.utc)).total_seconds() / 60
    if mins_to_start < -10:
        logger.debug(f"Descartado (partido empezó hace {-mins_to_start:.0f}min): {market.get('question','')[:50]}")
        return False
    if mins_to_start > MAX_ENTRY_WINDOW_H * 60:
        logger.debug(f"Descartado (partido en {mins_to_start/60:.0f}h > {MAX_ENTRY_WINDOW_H:.0f}h): {market.get('question','')[:50]}")
        return False
    return True


def get_market_id(market: dict) -> str:
    """Identificador único del mercado (conditionId o id)."""
    return market.get("conditionId") or market.get("id", "")


def check_positions(client, paper: bool = False):
    """Revisa posiciones abiertas y ejecuta TP/SL/TrailingStop si corresponde."""
    positions = load_positions(paper)
    if not positions:
        return

    # Deduplicar por token_id por si quedaron duplicados de instancias anteriores
    seen = set()
    unique_positions = []
    for p in positions:
        if p.token_id not in seen:
            seen.add(p.token_id)
            unique_positions.append(p)

    label = "[PAPER] " if paper else ""
    for pos in unique_positions:
        current_price = get_midpoint(pos.token_id)
        if current_price is None:
            continue

        # Actualizar precio pico (solo para trailing stop)
        if not paper:
            if current_price > _peak_prices.get(pos.token_id, 0):
                _peak_prices[pos.token_id] = current_price
        peak = _peak_prices.get(pos.token_id, pos.entry_price) if not paper else current_price

        change    = (current_price - pos.entry_price) / pos.entry_price
        peak_gain = (peak - pos.entry_price) / pos.entry_price

        # ── TP adaptativo según cercanía del partido (SOLO el TP) ──────────────
        # Cerca del partido tomamos beneficios algo antes. El SL NUNCA se aprieta:
        # un SL de 4% durante el juego convertía el ruido normal de un cuarto
        # (±5-10%) en pérdidas realizadas — el ajuste anterior era contraproducente.
        tp, sl = TAKE_PROFIT, STOP_LOSS
        if not paper:
            ref_str = _market_end_dates.get(pos.token_id, "")
            if ref_str:
                try:
                    ref_dt = datetime.fromisoformat(ref_str.replace("Z", "+00:00"))
                    if ref_dt.tzinfo is None:
                        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                    hours_left = (ref_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_left < 3:
                        tp = 0.05   # cerca del partido: asegura ganancias antes
                    elif hours_left < 8:
                        tp = 0.06
                    if hours_left < 8:
                        logger.debug(f"TP adaptativo: {hours_left:.1f}h → TP={tp*100:.0f}% (SL fijo {sl*100:.0f}%)")
                except Exception:
                    pass

        # ── Salida forzada por tiempo (posición atascada) — real Y paper ───────
        # El cierre por tiempo NO debe disparar antes de que el partido juegue:
        # una entrada 24-36h pre-partido salía en break-even sin ver la resolución
        # (su propia ventaja). Si el partido aún no ha terminado, extendemos el
        # deadline hasta GAME_OVER_BUFFER_H tras su inicio (reconstruido desde
        # opened_at + hours_to_start, que se guarda en cada posición).
        try:
            opened = datetime.fromisoformat(pos.opened_at).replace(tzinfo=timezone.utc)
            hold_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            deadline = MAX_HOLD_HOURS
            hrs_to_start = getattr(pos, "hours_to_start", 0.0) or 0.0
            if hrs_to_start > 0:
                # mantener hasta 6h después del inicio del partido (cubre prórroga/5 sets)
                deadline = max(MAX_HOLD_HOURS, hrs_to_start + GAME_OVER_BUFFER_H)
            if hold_hours >= deadline:
                execute_sell(
                    client, pos, current_price,
                    f"TIEMPO AGOTADO ({hold_hours:.1f}h > {deadline:.1f}h) {change*100:+.1f}%",
                    paper=paper
                )
                _mark_closed(_cooldown_key(pos.market_question, paper))
                if not paper:
                    _peak_prices.pop(pos.token_id, None)
                    _market_end_dates.pop(pos.token_id, None)
                continue
        except Exception:
            pass

        # ── Take profit ────────────────────────────────────────────────────────
        if change >= tp:
            execute_sell(client, pos, current_price, f"TAKE PROFIT +{change*100:.1f}% (TP={tp*100:.0f}%)", paper=paper)
            _mark_closed(_cooldown_key(pos.market_question, paper))
            if not paper:
                _peak_prices.pop(pos.token_id, None)
                _market_end_dates.pop(pos.token_id, None)

        # ── Trailing stop (solo real, activo tras ganar TRAILING_START%) ───────
        elif not paper and peak_gain >= TRAILING_START:
            trail_drop = (peak - current_price) / peak
            if trail_drop >= TRAILING_STOP:
                execute_sell(
                    client, pos, current_price,
                    f"TRAILING STOP (pico {peak:.3f} → {current_price:.3f}, -{trail_drop*100:.1f}% del pico)",
                    paper=False
                )
                _mark_closed(_cooldown_key(pos.market_question, paper=False))
                _peak_prices.pop(pos.token_id, None)
                _market_end_dates.pop(pos.token_id, None)
            else:
                logger.info(
                    f"Manteniendo | {pos.market_question[:50]} | "
                    f"{pos.entry_price:.3f}→{current_price:.3f} ({change*100:+.1f}%) "
                    f"[🔒 TRAIL pico={peak:.3f} margen={trail_drop*100:.1f}%/{TRAILING_STOP*100:.1f}%]"
                )

        # ── Stop loss ─────────────────────────────────────────────────────────
        elif change <= -sl:
            execute_sell(client, pos, current_price, f"STOP LOSS {change*100:.1f}% (SL={sl*100:.0f}%)", paper=paper)
            _mark_closed(_cooldown_key(pos.market_question, paper))
            if not paper:
                _peak_prices.pop(pos.token_id, None)
                _market_end_dates.pop(pos.token_id, None)

        # ── Mantener ──────────────────────────────────────────────────────────
        else:
            logger.info(
                f"{label}Manteniendo | {pos.market_question[:50]} | "
                f"{pos.entry_price:.3f}→{current_price:.3f} ({change*100:+.1f}%)"
            )


def market_has_time_left(market: dict) -> bool:
    """
    True si el mercado cierra en al menos MIN_HOURS_ENTRY horas.
    Evita entrar en mercados ya en curso o a punto de terminar.

    CLAVE: la referencia es gameStartTime (hora real del partido). NUNCA usar
    endDateIso: es SOLO la fecha (medianoche), así que un partido de tarde/noche
    parecía 'cerrado hace 17h' y se rechazaba — bug que estranguló el flujo de
    trades del Mundial. Fallback: endDate (lleva hora completa).
    """
    ref = get_game_start(market)
    if ref is None:
        end_str = market.get("endDate") or market.get("endDateIso") or ""
        if not end_str:
            return True
        try:
            ref = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return True
    hours_left = (ref - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours_left < MIN_HOURS_ENTRY:
        logger.debug(
            f"Descartado (solo {hours_left:.1f}h hasta el partido — posible en-juego): "
            f"{market.get('question','')[:50]}"
        )
        return False
    return True


MIN_STABILITY_OBS = 2   # 2 obs = ~5-10 min de datos. Bajado de 3 para capturar más
                        # partidos del Mundial que aparecen y empiezan rápido. El filtro
                        # de volatilidad sigue rechazando precios que se mueven demasiado.

def is_price_stable(market_id: str, yes_price: float, price_history: dict) -> bool:
    """
    True si el precio ha sido estable en los últimos scans.
    Requiere al menos MIN_STABILITY_OBS observaciones para poder apostar.
    Rechaza si la volatilidad supera MAX_PRICE_VOLATILITY.
    """
    now = time.time()
    history = price_history.setdefault(market_id, [])
    history.append((now, yes_price))
    # Mantiene solo los últimos 30 minutos
    price_history[market_id] = [(t, p) for t, p in history if now - t < 1800]
    prices = [p for _, p in price_history[market_id]]
    if len(prices) < MIN_STABILITY_OBS:
        falta = (MIN_STABILITY_OBS - len(prices)) * 5
        logger.debug(f"Esperando datos ({len(prices)}/{MIN_STABILITY_OBS} obs, ~{falta}min más): {market_id[:25]}")
        return False
    volatility = max(prices) - min(prices)
    if volatility > config.MAX_PRICE_VOLATILITY:
        logger.debug(f"Descartado (volatilidad {volatility:.3f} > {config.MAX_PRICE_VOLATILITY}): {market_id[:20]}")
        return False
    return True


def market_ends_today(market: dict) -> bool:
    """True si el mercado termina hoy (para paper trading agresivo)."""
    end_str = market.get("endDateIso") or market.get("endDate", "")
    if not end_str:
        return False
    try:
        end_date = datetime.fromisoformat(end_str[:10])
        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        return end_date.date() <= tomorrow
    except (ValueError, TypeError):
        return False


def is_any_active_market(market: dict) -> bool:
    """Para paper trading — acepta cualquier mercado activo excepto política, crypto y O/U."""
    question = market.get("question", "").lower()
    if any(kw in question for kw in POLITICS_KEYWORDS):
        return False
    if any(kw in question for kw in CRYPTO_KEYWORDS):
        return False
    if any(kw in question for kw in NON_WINNER_KEYWORDS):
        return False
    return True


def scan_markets(client, bet_market_ids: set, bet_match_keys: set,
                 price_history: dict, paper: bool = False) -> tuple[set, set]:
    """Busca nuevas oportunidades. paper=True usa reglas más agresivas sin dinero real."""

    if not paper and (daily_loss_exceeded() or weekly_loss_exceeded()):
        logger.info("Scan omitido — límite de pérdida diaria o semanal alcanzado.")
        return bet_market_ids, bet_match_keys

    label = "[PAPER] " if paper else ""
    markets = get_active_markets(limit=150)  # más mercados para más oportunidades
    open_positions = load_positions(paper)
    open_token_ids = {p.token_id for p in open_positions}
    new_bets = 0

    # Límite de posiciones concurrentes (solo real) — calidad > cantidad
    if not paper and len(open_positions) >= MAX_CONCURRENT:
        logger.info(
            f"Máx posiciones concurrentes alcanzado ({len(open_positions)}/{MAX_CONCURRENT}) "
            f"— scan omitido hasta que cierre alguna."
        )
        return bet_market_ids, bet_match_keys

    for market in markets:
        if paper:
            # Paper: mismos filtros de calidad que real (esports, torneos, cripto bloqueados)
            # Así los datos paper reflejan fielmente lo que haría el bot con dinero real
            if not market_ends_today(market): continue
            if not is_any_active_market(market): continue
            if is_esports_market(market): continue           # esports → datos contaminados
            if is_tournament_winner_market(market): continue # torneos → incertidumbre multi-semana
            if is_crypto_market(market): continue
            # Filtros temporales IGUALES que real — sin esto paper entraba 9 min
            # antes de un partido (Golden Knights 12-jun: −$1.04 por gap inicial)
            # y sus datos no validaban lo que el bot real haría de verdad
            if not market_has_time_left(market): continue    # ≥1.5h hasta endDate
            if not game_entry_window_ok(market): continue    # pre-partido (gameStartTime)
            # Descarta mercados ya resueltos (precio en 0.99+ o 0.01-)
            y_p, n_p = get_prices_from_market(market)
            if y_p is None: continue
            if y_p >= 0.95 or y_p <= 0.05: continue  # ya resuelto o a punto de resolver
            vol = float(market.get("volume24hr") or 0)
            if vol < 1000: continue
        else:
            # Real: filtros de calidad en cascada
            if not market_ends_by_tomorrow(market): continue
            if not market_has_time_left(market): continue       # endDate en < 1.5h (sanity)
            if is_crypto_market(market): continue
            if is_esports_market(market): continue              # esports → prohibido real money
            if is_tournament_winner_market(market): continue    # torneos → demasiada incertidumbre
            if not is_winner_sports_market(market): continue
            if not has_enough_liquidity(market): continue
            if not market_is_mature(market): continue       # < 30min de vida → precio sin asentar
            # Ventana de entrada PRE-PARTIDO sobre gameStartTime (el dato fiable)
            if not game_entry_window_ok(market): continue
            # Fallback SOLO si no hay gameStartTime: ventana 36h sobre endDate.
            # endDate (con hora) primero — endDateIso es solo fecha (medianoche).
            if get_game_start(market) is None:
                end_str = market.get("endDate") or market.get("endDateIso") or ""
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                        if hours_left > MAX_ENTRY_WINDOW_H:
                            logger.debug(f"Descartado (cierra en {hours_left:.0f}h > {MAX_ENTRY_WINDOW_H:.0f}h): {market.get('question','')[:50]}")
                            continue
                    except Exception:
                        pass

        market_id = get_market_id(market)
        if market_id in bet_market_ids: continue

        match_key = get_match_key(market)
        if match_key in bet_match_keys:
            logger.debug(f"Ya apostado: {match_key[:55]}")
            continue

        yes_price, no_price = get_prices_from_market(market)
        if yes_price is None: continue

        # Descarta mercados ya resueltos o casi resueltos (zona peligrosa > 92% / < 8%)
        if yes_price >= 0.92 or yes_price <= 0.08:
            logger.debug(f"Descartado (casi resuelto YES={yes_price:.2f}): {market.get('question','')[:50]}")
            continue

        # Filtro de estabilidad de precio — historial separado por modo:
        # real y paper escanean el mismo mercado en el mismo ciclo; con clave
        # compartida cada ciclo añadía 2 observaciones y el warm-up de 15 min
        # se quedaba en 10. Con prefijo, cada modo acumula 1 obs/5min de verdad.
        ph_key = f"{'p' if paper else 'r'}:{market_id}"
        if not is_price_stable(ph_key, yes_price, price_history): continue

        clob_ids = market.get("clobTokenIds") or []
        if len(clob_ids) >= 2 and not market.get("tokens"):
            market["tokens"] = [
                {"outcome": "YES", "token_id": clob_ids[0]},
                {"outcome": "NO",  "token_id": clob_ids[1]},
            ]

        # Inyecta metadatos en el market para estrategias
        hist_prices = [p for _, p in price_history.get(ph_key, [])]
        market["_price_history"] = hist_prices
        market["_paper"] = paper

        # Deporte: detección + flag para estrategias (ALWAYS_NO se desactiva en deportes)
        sport = _detect_sport(market)
        is_sports = is_winner_sports_market(market) or (sport is not None)
        market["_is_sports"] = is_sports
        market["_sport"] = sport   # siempre registrado (metadato); el boost solo aplica en real
        market["_sport_boost"] = SPORT_CONF_BOOST.get(sport, 0.0) if (sport and not paper) else 0.0
        # Mercado de empate (¿X vs Y acaba en empate?) — nuestra mayor ventaja al
        # comprar el NO. Boost de confianza aplicado en evaluate() para ambos modos.
        market["_is_draw_market"] = is_draw_market(market)
        # Horas hasta el partido — metadato para análisis de rendimiento
        gs = get_game_start(market)
        market["_hours_to_start"] = round((gs - datetime.now(timezone.utc)).total_seconds() / 3600, 2) if gs else 0.0

        q = market.get("question", "")
        no_str = f"{no_price:.3f}" if no_price else "?"
        sport_tag = f" [{sport}]" if sport else ""
        logger.debug(f"{'[PAPER] ' if paper else '[REAL]  '}Candidato{sport_tag}: {q[:50]} | YES={yes_price:.3f} NO={no_str}")

        # En paper: prueba ALWAYS_NO también en mercados de eventos no deportivos
        if paper and not is_sports:
            from strategy import always_no_strategy
            signal = always_no_strategy(market, yes_price, no_price)
            if signal.action == "HOLD":
                continue
        else:
            signal = evaluate(market, yes_price, no_price)

        if signal.action == "HOLD":
            continue

        # Multiplicador de tamaño para categorías con ventaja DEMOSTRADA — concentra
        # capital donde el bot gana, sin subir el riesgo global (límites intactos):
        #   · NO al empate en fútbol  (3/4 ganados, mejores P&L)
        #   · favorito de tenis pre-partido (tesis sólida + sin empates posibles)
        edge_mult = 1.0
        if signal.action == "BUY_NO" and market.get("_is_draw_market"):
            edge_mult = 1.25
        elif signal.action == "BUY_YES" and sport == "tennis":
            edge_mult = 1.25
        market["_edge_size_mult"] = edge_mult

        # Log de señal real generada — diagnóstico clave
        if not paper:
            edge_tag = f" ⭐x{edge_mult}" if edge_mult > 1.0 else ""
            logger.info(
                f"🎯 Señal real: [{signal.strategy}] {signal.action} | "
                f"conf={signal.confidence:.2f} | precio={signal.price:.3f}{edge_tag} | "
                f"token={'OK' if signal.token_id else '⚠️ VACÍO'} | {q[:45]}"
            )

        # Verificar que el TP es matemáticamente alcanzable PARA EL TOKEN específico
        MAX_ENTRY = round(0.97 / (1 + TAKE_PROFIT), 2)  # = 0.90 con TP=7%
        if signal.price > MAX_ENTRY:
            logger.info(f"❌ Descartado (TP inalcanzable {signal.price:.3f} > {MAX_ENTRY}): {q[:50]}")
            continue

        # Sin token_id no se puede operar
        if not signal.token_id:
            logger.warning(f"⚠️ Señal sin token_id — mercado sin clobTokenIds?: {q[:55]}")
            continue

        # Cooldown: no re-entrar al mismo mercado en las 4h tras un TP/SL
        if _is_in_cooldown(_cooldown_key(market.get("question", ""), paper)):
            logger.info(f"⏳ Cooldown activo (<{REENTRY_COOLDOWN_H:.0f}h desde cierre): {match_key[:55]}")
            continue

        if signal.token_id not in open_token_ids:
            question = market.get("question", "")
            wc = is_world_cup(market)
            if wc:
                logger.info(f"⚽ MUNDIAL detectado: {question[:55]}")
            ok = execute_signal(client, signal, question, market, paper=paper)
            if ok:
                bet_market_ids.add(market_id)
                bet_match_keys.add(match_key)
                open_token_ids.add(signal.token_id)
                new_bets += 1
                # Referencia temporal para el TP adaptativo: inicio del partido
                # (gameStartTime); endDate solo como fallback (en MLB es +7 días)
                if not paper:
                    gs = get_game_start(market)
                    ref = gs.isoformat() if gs else (market.get("endDateIso") or market.get("endDate", ""))
                    if ref:
                        _market_end_dates[signal.token_id] = ref

    winner_markets = sum(1 for m in markets if market_ends_by_tomorrow(m) and is_winner_sports_market(m))
    daily_loss = get_daily_loss()

    # Diagnóstico: muestra los mejores candidatos con sus precios aunque no se apostara
    if new_bets == 0 and not paper:
        candidates = []
        for m in markets:
            if not market_ends_by_tomorrow(m): continue
            if is_crypto_market(m): continue
            if not is_winner_sports_market(m): continue
            yp, np_ = get_prices_from_market(m)
            if yp and 0.08 < yp < 0.92:
                candidates.append((m.get("question","")[:50], yp, np_))
        if candidates:
            top = sorted(candidates, key=lambda x: abs(x[1] - 0.72))[:5]  # más cercanos al centro
            lines = " | ".join(f"{q}(Y={y:.2f})" for q, y, _ in top)
            logger.info(f"Sin señal — candidatos más cercanos al rango: {lines}")
        else:
            logger.info("Sin señal — ningún mercado deportivo en rango de precio (0.08-0.92)")

    logger.info(
        f"{label}Scan completado | {winner_markets} deportes elegibles | {new_bets} apuestas | "
        f"Estrategias: {','.join(config.STRATEGIES_ACTIVE)} | "
        f"Pérdida diaria: -${daily_loss:.2f}/-${config.MAX_DAILY_LOSS_USDC:.2f}"
    )
    return bet_market_ids, bet_match_keys


def _rebuild_match_keys_from_history(paper: bool) -> set:
    """
    Reconstruye el conjunto de match_keys apostados desde el historial completo.
    Evita re-apostar mercados ya cerrados por TP/SL tras un reinicio del bot.
    Solo incluye operaciones recientes (últimas 12h) para no bloquear mercados futuros.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    keys = set()
    for h in load_history(paper):
        try:
            closed = datetime.fromisoformat(h.get("closed_at", "2000-01-01")).replace(tzinfo=timezone.utc)
            if closed >= cutoff:
                keys.add(get_match_key({"question": h.get("market_question", "")}))
        except (ValueError, TypeError):
            pass
    return keys


def _write_heartbeat(client=None):
    """Escribe heartbeat.json y lo sube a GitHub. El dashboard lo lee para saber si el bot está vivo."""
    try:
        balance_usdc = get_real_balance(client) if client else None
        open_real    = len(load_positions(paper=False))
        open_paper   = len(load_positions(paper=True))
        realized_real = sum(h.get("pnl", 0) for h in load_history(paper=False))
        paper_history  = [h for h in load_history(paper=True) if h.get("result") != "GHOST"]
        realized_paper = sum(h.get("pnl", 0) for h in paper_history)
        paper_balance  = round(config.PAPER_STARTING_BALANCE + realized_paper, 2)
        paper_roi      = round(realized_paper / config.PAPER_STARTING_BALANCE * 100, 2)
        weekly_loss    = get_weekly_loss()
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "interval_s": SCAN_MARKETS_S,
            "balance_usdc": round(balance_usdc, 2) if balance_usdc is not None else None,
            "open_real": open_real,
            "open_paper": open_paper,
            "realized_pnl_real": round(realized_real, 4),
            "realized_pnl_paper": round(realized_paper, 4),
            "paper_balance": paper_balance,
            "paper_roi_pct": paper_roi,
            "paper_starting_balance": config.PAPER_STARTING_BALANCE,
            "weekly_loss_usdc": round(weekly_loss, 2),
            "weekly_loss_limit": config.MAX_WEEKLY_LOSS_USDC,
        }
        with open("heartbeat.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f)
        from positions import _push_to_github
        _push_to_github(["heartbeat.json"])
    except Exception as e:
        logger.debug(f"Heartbeat omitido: {e}")


def main():
    # Timeout global de sockets: la librería del CLOB (py-clob-client) hace
    # llamadas HTTP SIN timeout — una conexión muerta colgaba el loop entero
    # para siempre (pasó el 10-jun: 20+ min congelado tras un scan).
    # Con esto, cualquier socket sin timeout explícito muere a los 20s.
    import socket
    socket.setdefaulttimeout(20)

    # OJO: setdefaulttimeout NO basta para `requests` — pasa timeout=None
    # explícito hasta socket.create_connection, que con None = bloqueo infinito.
    # (Segundo cuelgue del 10-jun: congelado dentro del POST de una orden FOK.)
    # Parche global: cualquier requests sin timeout recibe 20s.
    import requests as _rq
    _orig_request = _rq.Session.request
    def _request_with_timeout(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 20
        return _orig_request(self, method, url, **kwargs)
    _rq.Session.request = _request_with_timeout

    # Lock file — evita múltiples instancias simultáneas
    import msvcrt
    lock_path = "bot.lock"
    try:
        lock_file = open(lock_path, "w")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        logger.error("Ya hay una instancia del bot corriendo. Saliendo.")
        sys.exit(2)  # Código 2 = duplicado, el .bat no reinicia

    if not config.PRIVATE_KEY and not config.DRY_RUN:
        logger.error("PRIVATE_KEY no configurada y DRY_RUN=false. Abortando.")
        sys.exit(1)

    _load_closed_cooldown()   # carga cooldown de re-entrada desde disco
    start_log_server()
    client = build_client() if not config.DRY_RUN else None
    if client:
        get_real_balance(client)  # precarga el balance al arrancar

    logger.info(f"Bot iniciado | TP: +{TAKE_PROFIT*100:.0f}% | SL: -{STOP_LOSS*100:.0f}% | "
                f"TP/SL continuo cada {SCAN_POSITIONS_S}s | Scan mercados cada {SCAN_MARKETS_S}s")

    existing = load_positions()
    # bet_market_ids usa conditionId (no token_id) para deduplicar correctamente
    bet_market_ids:  set = set()   # se rellena progresivamente en scan_markets
    # Reconstruye desde historial para no re-apostar mercados ya cerrados tras reinicio
    bet_match_keys:  set = (
        {get_match_key({"question": p.market_question}) for p in existing}
        | _rebuild_match_keys_from_history(paper=False)
    )

    # Paper trading — estado separado
    paper_existing      = load_positions(paper=True)
    paper_bet_ids:  set = {p.token_id for p in paper_existing}
    paper_match_keys: set = (
        {get_match_key({"question": p.market_question}) for p in paper_existing}
        | _rebuild_match_keys_from_history(paper=True)
    )

    price_history: dict = {}  # {market_id: [(timestamp, price), ...]}
    last_market_scan = 0

    if config.PAPER_TRADING:
        logger.info("📝 Paper trading activado — simulación paralela en paper_positions.json")

    logger.info("Bot corriendo en modo 24/7 — sin límite de tiempo. Usa Ctrl+C para detener.")
    start_watchdog()
    try:
        while True:
            now = time.time()
            _last_tick["t"] = now   # señal de vida para el watchdog

            if now - last_market_scan >= SCAN_MARKETS_S:
                logger.info("=== SCAN MERCADOS ===")
                bet_market_ids, bet_match_keys = scan_markets(
                    client, bet_market_ids, bet_match_keys, price_history, paper=False
                )
                if config.PAPER_TRADING:
                    paper_bet_ids, paper_match_keys = scan_markets(
                        None, paper_bet_ids, paper_match_keys, price_history, paper=True
                    )
                _write_heartbeat(client)
                last_market_scan = now

            # TP/SL continuo cada 3 segundos
            check_positions(client, paper=False)
            if config.PAPER_TRADING:
                check_positions(None, paper=True)

            # Refresca balance cada ~60s
            if client and int(time.time()) % 60 < SCAN_POSITIONS_S:
                get_real_balance(client)

            time.sleep(SCAN_POSITIONS_S)

    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario (Ctrl+C).")
    except Exception as e:
        # Captura cualquier error inesperado — escribe heartbeat de emergencia y re-lanza
        logger.critical(f"ERROR CRÍTICO — bot va a reiniciarse: {e}", exc_info=True)
        try:
            _write_heartbeat(client)
        except Exception:
            pass
        raise  # el .bat lo reiniciará automáticamente


if __name__ == "__main__":
    main()

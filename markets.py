"""Obtiene mercados activos y sus precios desde la API de Polymarket."""
import json
import requests
from loguru import logger
from config import GAMMA_HOST, CLOB_HOST


def _parse_markets(markets: list) -> list:
    """Pre-parsea outcomePrices y clobTokenIds (vienen como strings JSON)."""
    for m in markets:
        if isinstance(m.get("outcomePrices"), str):
            try:
                m["outcomePrices"] = json.loads(m["outcomePrices"])
            except (ValueError, TypeError):
                m["outcomePrices"] = []
        if isinstance(m.get("clobTokenIds"), str):
            try:
                m["clobTokenIds"] = json.loads(m["clobTokenIds"])
            except (ValueError, TypeError):
                m["clobTokenIds"] = []
    return markets


def get_active_markets(limit: int = 100) -> list[dict]:
    """
    Devuelve mercados activos combinando dos fuentes:
    1. Top por volumen 24h (mercados populares, mayoría en curso)
    2. Los que empiezan pronto ordenados por fecha de inicio (pre-partido)
    Combina y deduplica por conditionId para dar más oportunidades al bot real.
    """
    seen_ids = set()
    all_markets = []

    # Fuente 1: top por volumen (paper trading y mercados activos)
    try:
        resp = requests.get(
            f"{GAMMA_HOST}/markets",
            params={"active": "true", "closed": "false", "limit": limit,
                    "order": "volume24hr", "ascending": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        for m in _parse_markets(resp.json()):
            mid = m.get("conditionId") or m.get("id", "")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                all_markets.append(m)
    except Exception as e:
        logger.error(f"Error obteniendo mercados (volumen): {e}")

    # Fuente 2: mercados que empiezan pronto — clave para apostar pre-partido en real
    try:
        resp = requests.get(
            f"{GAMMA_HOST}/markets",
            params={"active": "true", "closed": "false", "limit": limit,
                    "order": "startDate", "ascending": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        for m in _parse_markets(resp.json()):
            mid = m.get("conditionId") or m.get("id", "")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                all_markets.append(m)
    except Exception as e:
        logger.error(f"Error obteniendo mercados (startDate): {e}")

    return all_markets


def get_prices_from_market(market: dict) -> tuple[float | None, float | None]:
    """Extrae yes_price y no_price directamente del campo outcomePrices del mercado."""
    prices = market.get("outcomePrices") or []
    try:
        if len(prices) >= 2:
            return float(prices[0]), float(prices[1])
    except (ValueError, TypeError):
        pass
    return None, None


def get_midpoint(token_id: str) -> float | None:
    """Obtiene el precio medio desde el CLOB. Fallback cuando no hay outcomePrices."""
    try:
        resp = requests.get(f"{CLOB_HOST}/midpoint", params={"token_id": token_id}, timeout=10)
        if resp.ok:
            return float(resp.json().get("mid", 0)) or None
    except Exception:
        pass
    # Segundo fallback: orderbook
    try:
        resp = requests.get(f"{CLOB_HOST}/book", params={"token_id": token_id}, timeout=10)
        if resp.ok:
            book = resp.json()
            best_bid = float(book["bids"][0]["price"]) if book.get("bids") else None
            best_ask = float(book["asks"][0]["price"]) if book.get("asks") else None
            if best_bid and best_ask:
                return (best_bid + best_ask) / 2
            return best_bid or best_ask
    except Exception:
        pass
    return None


def get_bid_ask_spread(token_id: str) -> float | None:
    """
    Devuelve el spread bid-ask ABSOLUTO (puntos de probabilidad, escala 0.0–1.0).
    Para mercados de predicción el spread relativo ((ask-bid)/bid) infla el número:
      bid=0.57, ask=0.60 → relativo=5.3% (rechazaría!) vs absoluto=0.03 (3¢, OK)
    Umbral recomendado: 0.05 (5 puntos porcentuales = 5 centavos).
    Devuelve None si no hay datos → se asume líquido y se permite entrar.
    Devuelve 1.0 si solo hay un lado del libro → ilíquido, rechazar.
    """
    try:
        resp = requests.get(f"{CLOB_HOST}/book", params={"token_id": token_id}, timeout=8)
        if not resp.ok:
            return None
        book = resp.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        # Un lado del libro vacío = mercado sin contraparte real → ilíquido
        if not bids or not asks:
            return 1.0
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        if best_bid <= 0 or best_ask <= 0:
            return None
        # Spread absoluto: bid=0.57 ask=0.60 → 0.03 (3 centavos) ✓
        return best_ask - best_bid
    except Exception:
        pass
    return None

"""Obtiene mercados activos y sus precios desde la API de Polymarket."""
import requests
from loguru import logger
from config import GAMMA_HOST, CLOB_HOST


def get_active_markets(limit: int = 50) -> list[dict]:
    """Devuelve mercados activos con volumen > 0, ordenados por volumen."""
    try:
        resp = requests.get(
            f"{GAMMA_HOST}/markets",
            params={"active": "true", "closed": "false", "limit": limit, "order": "volume24hr", "ascending": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Error obteniendo mercados: {e}")
        return []


def get_market_price(token_id: str) -> dict | None:
    """Devuelve el mejor bid/ask para un token."""
    try:
        resp = requests.get(f"{CLOB_HOST}/price", params={"token_id": token_id, "side": "BUY"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Error obteniendo precio para {token_id}: {e}")
        return None


def get_orderbook(token_id: str) -> dict | None:
    """Devuelve el orderbook completo de un token."""
    try:
        resp = requests.get(f"{CLOB_HOST}/book", params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Error obteniendo orderbook para {token_id}: {e}")
        return None


def get_midpoint(token_id: str) -> float | None:
    """Calcula el precio medio entre el mejor bid y ask."""
    book = get_orderbook(token_id)
    if not book:
        return None
    try:
        best_bid = float(book["bids"][0]["price"]) if book.get("bids") else None
        best_ask = float(book["asks"][0]["price"]) if book.get("asks") else None
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return best_bid or best_ask
    except (KeyError, IndexError, ValueError):
        return None

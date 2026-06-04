"""Obtiene mercados activos y sus precios desde la API de Polymarket."""
import json
import requests
from loguru import logger
from config import GAMMA_HOST, CLOB_HOST


def get_active_markets(limit: int = 50) -> list[dict]:
    """Devuelve mercados activos ordenados por volumen 24h, con precios ya parseados."""
    try:
        resp = requests.get(
            f"{GAMMA_HOST}/markets",
            params={"active": "true", "closed": "false", "limit": limit, "order": "volume24hr", "ascending": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
        # Pre-parsea outcomePrices y clobTokenIds (vienen como strings JSON)
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
    except Exception as e:
        logger.error(f"Error obteniendo mercados: {e}")
        return []


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

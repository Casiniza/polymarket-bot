"""Gestiona posiciones abiertas. Las persiste en positions.json en el repo."""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from loguru import logger

POSITIONS_FILE = "positions.json"


@dataclass
class Position:
    token_id: str
    action: str          # "BUY_YES" | "BUY_NO"
    entry_price: float
    size: float          # cantidad de shares
    usdc_spent: float
    market_question: str
    opened_at: str       # ISO timestamp


def load_positions() -> list[Position]:
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r") as f:
            data = json.load(f)
        return [Position(**p) for p in data]
    except Exception as e:
        logger.error(f"Error cargando posiciones: {e}")
        return []


def save_positions(positions: list[Position]):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump([asdict(p) for p in positions], f, indent=2)
        logger.info(f"Posiciones guardadas: {len(positions)} abiertas")
    except Exception as e:
        logger.error(f"Error guardando posiciones: {e}")


def add_position(token_id: str, action: str, entry_price: float, size: float,
                 usdc_spent: float, market_question: str) -> Position:
    positions = load_positions()
    pos = Position(
        token_id=token_id,
        action=action,
        entry_price=entry_price,
        size=size,
        usdc_spent=usdc_spent,
        market_question=market_question,
        opened_at=datetime.utcnow().isoformat(),
    )
    positions.append(pos)
    save_positions(positions)
    return pos


def remove_position(token_id: str):
    positions = load_positions()
    positions = [p for p in positions if p.token_id != token_id]
    save_positions(positions)

"""Gestiona posiciones abiertas. Las persiste en positions.json y sincroniza con GitHub."""
import json
import os
import subprocess
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
        with open(POSITIONS_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return [Position(**p) for p in data]
    except Exception as e:
        logger.error(f"Error cargando posiciones: {e}")
        return []


def save_positions(positions: list[Position]):
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in positions], f, indent=2)
        logger.info(f"Posiciones guardadas: {len(positions)} abiertas")
        _push_to_github()
    except Exception as e:
        logger.error(f"Error guardando posiciones: {e}")


def _push_to_github():
    """Sube positions.json a GitHub para que el dashboard lo vea."""
    try:
        subprocess.run(["git", "add", POSITIONS_FILE], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode != 0:  # hay cambios staged
            subprocess.run(
                ["git", "commit", "-m", "chore: update positions [skip ci]"],
                check=True, capture_output=True
            )
            subprocess.run(["git", "push"], check=True, capture_output=True)
            logger.debug("positions.json sincronizado con GitHub")
    except Exception as e:
        logger.debug(f"Git push omitido: {e}")


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

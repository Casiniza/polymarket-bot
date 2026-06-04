"""Gestiona posiciones abiertas y historial. Sincroniza con GitHub para el dashboard."""
import json
import os
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from loguru import logger

POSITIONS_FILE = "positions.json"
HISTORY_FILE   = "history.json"


@dataclass
class Position:
    token_id: str
    action: str
    entry_price: float
    size: float
    usdc_spent: float
    market_question: str
    opened_at: str


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
        with open(POSITIONS_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump([asdict(p) for p in positions], f, indent=2)
        logger.info(f"Posiciones guardadas: {len(positions)} abiertas")
        _push_to_github([POSITIONS_FILE])
    except Exception as e:
        logger.error(f"Error guardando posiciones: {e}")


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return []


def record_closed(position: Position, exit_price: float, result: str):
    """Guarda una operación cerrada en history.json (TP o SL)."""
    pnl = round((exit_price - position.entry_price) * position.size, 4)
    history = load_history()
    history.append({
        "market_question": position.market_question,
        "action": position.action,
        "entry_price": position.entry_price,
        "exit_price": exit_price,
        "size": position.size,
        "usdc_spent": position.usdc_spent,
        "pnl": pnl,
        "result": result,   # "TP" o "SL"
        "opened_at": position.opened_at,
        "closed_at": datetime.utcnow().isoformat(),
    })
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(history, f, indent=2)
        _push_to_github([HISTORY_FILE])
    except Exception as e:
        logger.error(f"Error guardando historial: {e}")


def _push_to_github(files: list[str]):
    try:
        subprocess.run(["git", "add"] + files, check=True, capture_output=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: update bot data [skip ci]"],
                check=True, capture_output=True
            )
            subprocess.run(["git", "push"], check=True, capture_output=True)
            logger.debug("Datos sincronizados con GitHub")
    except Exception as e:
        logger.debug(f"Git push omitido: {e}")


def add_position(token_id: str, action: str, entry_price: float, size: float,
                 usdc_spent: float, market_question: str) -> Position:
    positions = load_positions()
    pos = Position(
        token_id=token_id, action=action, entry_price=entry_price,
        size=size, usdc_spent=usdc_spent, market_question=market_question,
        opened_at=datetime.utcnow().isoformat(),
    )
    positions.append(pos)
    save_positions(positions)
    return pos


def remove_position(token_id: str, exit_price: float = None, result: str = None):
    positions = load_positions()
    closing = next((p for p in positions if p.token_id == token_id), None)
    if closing and exit_price and result:
        record_closed(closing, exit_price, result)
    positions = [p for p in positions if p.token_id != token_id]
    save_positions(positions)

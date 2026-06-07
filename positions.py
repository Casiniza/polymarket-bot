"""Gestiona posiciones abiertas y historial. Sincroniza con GitHub para el dashboard."""
import json
import os
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from loguru import logger

POSITIONS_FILE       = "positions.json"
HISTORY_FILE         = "history.json"
PAPER_POSITIONS_FILE = "paper_positions.json"
PAPER_HISTORY_FILE   = "paper_history.json"


@dataclass
class Position:
    token_id: str
    action: str
    entry_price: float
    size: float
    usdc_spent: float
    market_question: str
    opened_at: str


def _positions_file(paper: bool) -> str:
    return PAPER_POSITIONS_FILE if paper else POSITIONS_FILE

def _history_file(paper: bool) -> str:
    return PAPER_HISTORY_FILE if paper else HISTORY_FILE


def load_positions(paper: bool = False) -> list[Position]:
    f = _positions_file(paper)
    if not os.path.exists(f):
        return []
    try:
        with open(f, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return [Position(**p) for p in data]
    except Exception as e:
        logger.error(f"Error cargando posiciones{'(paper)' if paper else ''}: {e}")
        return []


def save_positions(positions: list[Position], paper: bool = False):
    f = _positions_file(paper)
    try:
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            json.dump([asdict(p) for p in positions], fh, indent=2)
        label = "(paper)" if paper else ""
        logger.info(f"Posiciones{label} guardadas: {len(positions)} abiertas")
        _push_to_github([f])
    except Exception as e:
        logger.error(f"Error guardando posiciones: {e}")


def load_history(paper: bool = False) -> list[dict]:
    f = _history_file(paper)
    if not os.path.exists(f):
        return []
    try:
        with open(f, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return []


def record_closed(position: Position, exit_price: float, result: str, paper: bool = False):
    """Guarda una operación cerrada en history.json (TP o SL)."""
    pnl = round((exit_price - position.entry_price) * position.size, 4)
    history = load_history(paper)
    history.append({
        "market_question": position.market_question,
        "action": position.action,
        "entry_price": position.entry_price,
        "exit_price": exit_price,
        "size": position.size,
        "usdc_spent": position.usdc_spent,
        "pnl": pnl,
        "result": result,
        "paper": paper,
        "opened_at": position.opened_at,
        "closed_at": datetime.utcnow().isoformat(),
    })
    f = _history_file(paper)
    try:
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(history, fh, indent=2)
        _push_to_github([f])
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
                 usdc_spent: float, market_question: str, paper: bool = False) -> Position:
    positions = load_positions(paper)
    # Deduplicación final: nunca añadir un token_id que ya existe en el archivo
    if any(p.token_id == token_id for p in positions):
        logger.warning(f"Token ya registrado, ignorando apuesta duplicada: {market_question[:50]}")
        return positions[0]  # devuelve la existente
    pos = Position(
        token_id=token_id, action=action, entry_price=entry_price,
        size=size, usdc_spent=usdc_spent, market_question=market_question,
        opened_at=datetime.utcnow().isoformat(),
    )
    positions.append(pos)
    save_positions(positions, paper)
    return pos


def remove_position(token_id: str, exit_price: float = None, result: str = None, paper: bool = False):
    positions = load_positions(paper)
    closing = next((p for p in positions if p.token_id == token_id), None)
    if closing and exit_price and result:
        record_closed(closing, exit_price, result, paper)
    positions = [p for p in positions if p.token_id != token_id]
    save_positions(positions, paper)

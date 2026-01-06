"""
Logging Configuration
Sets up logging for the bot
"""
import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_to_file: bool = True) -> logging.Logger:
    """Configure logging for the bot"""

    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Create logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    logger.handlers = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler
    if log_to_file:
        log_filename = log_dir / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

        logging.info(f"Logging to file: {log_filename}")

    return logger


def log_trade(
    strategy: str,
    side: str,
    size: float,
    price: float,
    reason: str,
    pnl: float = 0.0
):
    """Log a trade execution"""
    logger = logging.getLogger("trades")

    trade_info = {
        "strategy": strategy,
        "side": side,
        "size": size,
        "price": price,
        "reason": reason,
        "pnl": pnl,
        "timestamp": datetime.now().isoformat()
    }

    if pnl != 0:
        emoji = "+" if pnl > 0 else ""
        logger.info(f"TRADE | {strategy} | {side} {size}@{price} | PnL: {emoji}{pnl:.4f} | {reason}")
    else:
        logger.info(f"TRADE | {strategy} | {side} {size}@{price} | {reason}")


def log_status(
    equity: float,
    exposure: float,
    unrealized_pnl: float,
    open_orders: int,
    positions: int
):
    """Log bot status"""
    logger = logging.getLogger("status")
    pnl_str = f"+{unrealized_pnl:.4f}" if unrealized_pnl >= 0 else f"{unrealized_pnl:.4f}"
    logger.info(
        f"STATUS | Equity: ${equity:.2f} | Exposure: ${exposure:.2f} | "
        f"uPnL: ${pnl_str} | Orders: {open_orders} | Positions: {positions}"
    )

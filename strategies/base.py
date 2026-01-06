"""
Base Strategy Class
Abstract base class for all trading strategies
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime

from src.market_data import OrderBook, MarketInfo
from src.order_manager import OrderManager, OrderSide, Position
from config import TradingConfig

logger = logging.getLogger(__name__)


@dataclass
class StrategyState:
    """Track strategy state and performance"""
    name: str
    enabled: bool = True
    trades_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: Decimal = Decimal("0")
    last_trade_time: Optional[datetime] = None
    consecutive_losses: int = 0
    in_cooldown: bool = False
    cooldown_until: Optional[datetime] = None

    @property
    def win_rate(self) -> Decimal:
        if self.trades_count > 0:
            return Decimal(self.wins) / Decimal(self.trades_count) * 100
        return Decimal("0")


class BaseStrategy(ABC):
    """Base class for all trading strategies"""

    def __init__(
        self,
        name: str,
        order_manager: OrderManager,
        trading_config: TradingConfig,
        market_id: int = 0
    ):
        self.name = name
        self.order_manager = order_manager
        self.config = trading_config
        self.market_id = market_id
        self.state = StrategyState(name=name)
        self._last_orderbook: Optional[OrderBook] = None
        self._market_info: Optional[MarketInfo] = None

    @abstractmethod
    async def on_orderbook_update(self, orderbook: OrderBook):
        """Called when orderbook is updated"""
        pass

    @abstractmethod
    async def evaluate(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate market conditions and return trading signal if any.
        Returns dict with: side, price, size, order_type, reason
        """
        pass

    async def execute_signal(self, signal: Dict[str, Any]) -> bool:
        """Execute a trading signal"""
        try:
            side = OrderSide.BUY if signal["side"] == "buy" else OrderSide.SELL
            price = Decimal(str(signal["price"]))
            size = Decimal(str(signal["size"]))

            order = await self.order_manager.place_limit_order(
                market_id=self.market_id,
                side=side,
                price=price,
                size=size,
                strategy=self.name,
                post_only=signal.get("post_only", True)
            )

            if order:
                self.state.trades_count += 1
                self.state.last_trade_time = datetime.now()
                logger.info(f"[{self.name}] Executed: {signal['reason']}")
                return True

        except Exception as e:
            logger.error(f"[{self.name}] Failed to execute signal: {e}")
        return False

    def update_orderbook(self, orderbook: OrderBook):
        """Update cached orderbook"""
        self._last_orderbook = orderbook

    def update_market_info(self, market_info: MarketInfo):
        """Update market info"""
        self._market_info = market_info

    def record_trade_result(self, pnl: Decimal):
        """Record trade result for tracking"""
        self.state.total_pnl += pnl

        if pnl > 0:
            self.state.wins += 1
            self.state.consecutive_losses = 0
        else:
            self.state.losses += 1
            self.state.consecutive_losses += 1

            # Check for cooldown
            if self.state.consecutive_losses >= self.config.max_consecutive_losses:
                self.state.in_cooldown = True
                self.state.cooldown_until = datetime.now()
                logger.warning(f"[{self.name}] Entering cooldown after {self.state.consecutive_losses} losses")

    def is_enabled(self) -> bool:
        """Check if strategy is enabled and not in cooldown"""
        if not self.state.enabled:
            return False

        if self.state.in_cooldown:
            if self.state.cooldown_until:
                elapsed = (datetime.now() - self.state.cooldown_until).total_seconds()
                if elapsed >= self.config.cooldown_after_loss_seconds:
                    self.state.in_cooldown = False
                    self.state.consecutive_losses = 0
                    logger.info(f"[{self.name}] Exiting cooldown")
                else:
                    return False
        return True

    def calculate_position_size(self, price: Decimal) -> Decimal:
        """Calculate position size based on risk parameters"""
        # Risk per trade in USD (2% of $100 = $2)
        risk_usd = Decimal(str(self.config.risk_per_trade_pct)) / 100 * Decimal("100")

        # Size based on notional value - aim for ~$15-20 notional to meet minimums
        max_size_usd = min(
            Decimal(str(self.config.max_position_usd)),
            max(Decimal("15"), risk_usd * Decimal(str(self.config.default_leverage)))
        )

        # Convert to base asset size
        if price > 0:
            size = max_size_usd / price
            # Round to reasonable precision
            return size.quantize(Decimal("0.0001"))
        return Decimal("0")

    async def cleanup(self):
        """Cancel all orders for this strategy"""
        await self.order_manager.cancel_all_orders(
            market_id=self.market_id,
            strategy=self.name
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics"""
        return {
            "name": self.name,
            "enabled": self.state.enabled,
            "trades": self.state.trades_count,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "win_rate": float(self.state.win_rate),
            "total_pnl": float(self.state.total_pnl),
            "consecutive_losses": self.state.consecutive_losses,
            "in_cooldown": self.state.in_cooldown
        }

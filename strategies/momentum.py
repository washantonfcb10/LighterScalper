"""
Momentum Strategy
Follows short-term price momentum for quick scalps
"""
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import datetime
from collections import deque

from strategies.base import BaseStrategy
from src.market_data import OrderBook
from src.order_manager import OrderSide

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    Simple momentum strategy:
    - Tracks mid-price changes
    - Enters in direction of momentum
    - Quick exits with small targets
    - Conservative for small capital
    """

    def __init__(self, order_manager, trading_config, market_id: int = 0):
        super().__init__(
            name="momentum",
            order_manager=order_manager,
            trading_config=trading_config,
            market_id=market_id
        )
        self._price_history: deque = deque(maxlen=30)
        self._last_signal_time: Optional[datetime] = None
        self._min_signal_interval = 45.0  # seconds (was 15) - reduce overtrading
        self._current_order_id: Optional[str] = None

    async def on_orderbook_update(self, orderbook: OrderBook):
        """Track price changes"""
        self.update_orderbook(orderbook)

        if orderbook.mid_price:
            self._price_history.append({
                "price": float(orderbook.mid_price),
                "time": datetime.now()
            })

    async def evaluate(self) -> Optional[Dict[str, Any]]:
        """Look for momentum signals"""
        if not self.is_enabled():
            return None

        if not self._last_orderbook:
            return None

        # Rate limit
        if self._last_signal_time:
            elapsed = (datetime.now() - self._last_signal_time).total_seconds()
            if elapsed < self._min_signal_interval:
                return None

        # Check for existing position - manage it first
        position = self.order_manager.get_position(self.market_id)
        if position:
            return await self._manage_position()

        # Need enough price history
        if len(self._price_history) < 15:
            return None

        signal = self._analyze_momentum()

        if signal:
            self._last_signal_time = datetime.now()

        return signal

    def _analyze_momentum(self) -> Optional[Dict[str, Any]]:
        """Analyze price momentum"""
        prices = list(self._price_history)

        if len(prices) < 15:
            return None

        # Calculate short-term and medium-term momentum
        recent_prices = [p["price"] for p in prices[-5:]]
        older_prices = [p["price"] for p in prices[-15:-5]]

        recent_avg = sum(recent_prices) / len(recent_prices)
        older_avg = sum(older_prices) / len(older_prices)

        # Calculate momentum as percentage change
        if older_avg == 0:
            return None

        momentum_pct = (recent_avg - older_avg) / older_avg * 100

        # Need strong momentum signal (at least 0.10% - was 0.03%)
        if abs(momentum_pct) < 0.10:
            return None

        ob = self._last_orderbook
        if not ob or not ob.mid_price:
            return None

        # Calculate position size - very conservative
        size = self.calculate_position_size(ob.mid_price)
        size = size * Decimal("0.2")  # Ultra conservative for momentum

        if size <= 0:
            return None

        # Determine direction and entry
        if momentum_pct > 0.10:
            # Upward momentum - buy
            # Place limit order slightly below mid to get filled
            entry_price = ob.mid_price * Decimal("0.9998")
            side = "buy"
            reason = f"Bullish momentum: {momentum_pct:.3f}%"
        else:
            # Downward momentum - sell
            entry_price = ob.mid_price * Decimal("1.0002")
            side = "sell"
            reason = f"Bearish momentum: {momentum_pct:.3f}%"

        # Round price
        tick = Decimal("0.01")
        entry_price = (entry_price / tick).quantize(Decimal("1")) * tick

        logger.info(f"[{self.name}] Signal: {reason}")

        return {
            "side": side,
            "price": entry_price,
            "size": size,
            "post_only": True,
            "reason": reason
        }

    async def _manage_position(self) -> Optional[Dict[str, Any]]:
        """Manage existing position"""
        position = self.order_manager.get_position(self.market_id)
        if not position:
            return None

        ob = self._last_orderbook
        if not ob:
            return None

        # Calculate targets - wider to avoid noise stops
        entry_value = position.notional_value
        target_profit_pct = Decimal("0.002")  # 0.2% target (was 0.05%)
        stop_loss_pct = Decimal("0.004")  # 0.4% stop (was 0.1%)

        target_profit = entry_value * target_profit_pct
        max_loss = entry_value * stop_loss_pct

        # Check for take profit
        if position.unrealized_pnl >= target_profit:
            exit_price = ob.best_bid if position.side == OrderSide.BUY else ob.best_ask
            return {
                "side": "sell" if position.side == OrderSide.BUY else "buy",
                "price": exit_price,
                "size": position.size,
                "post_only": False,
                "reduce_only": True,
                "reason": f"Momentum TP: +{position.unrealized_pnl:.4f} USD"
            }

        # Check for stop loss
        if position.unrealized_pnl <= -max_loss:
            exit_price = ob.best_bid if position.side == OrderSide.BUY else ob.best_ask
            return {
                "side": "sell" if position.side == OrderSide.BUY else "buy",
                "price": exit_price,
                "size": position.size,
                "post_only": False,
                "reduce_only": True,
                "reason": f"Momentum SL: {position.unrealized_pnl:.4f} USD"
            }

        return None

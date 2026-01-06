"""
Spread Scalper Strategy
Captures profits from spread when conditions are favorable
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


class SpreadScalperStrategy(BaseStrategy):
    """
    Spread scalping strategy:
    - Monitors spread conditions
    - Enters when spread is wide and expected to narrow
    - Uses order book imbalance for direction
    - Quick in-and-out trades
    """

    def __init__(self, order_manager, trading_config, market_id: int = 0):
        super().__init__(
            name="spread_scalper",
            order_manager=order_manager,
            trading_config=trading_config,
            market_id=market_id
        )
        self._spread_history: deque = deque(maxlen=20)
        self._imbalance_history: deque = deque(maxlen=20)
        self._last_signal_time: Optional[datetime] = None
        self._min_signal_interval = 30.0  # seconds (was 10) - reduce overtrading

    async def on_orderbook_update(self, orderbook: OrderBook):
        """Track orderbook changes"""
        self.update_orderbook(orderbook)

        if orderbook.spread_bps:
            self._spread_history.append(float(orderbook.spread_bps))
        if orderbook.imbalance:
            self._imbalance_history.append(float(orderbook.imbalance))

    async def evaluate(self) -> Optional[Dict[str, Any]]:
        """Look for spread scalping opportunities"""
        if not self.is_enabled():
            return None

        if not self._last_orderbook:
            return None

        ob = self._last_orderbook

        # FIRST: Check if we need to exit existing position
        exit_signal = self._check_exit_conditions(ob)
        if exit_signal:
            self._last_signal_time = datetime.now()
            return exit_signal

        # Rate limit new entry signals
        if self._last_signal_time:
            elapsed = (datetime.now() - self._last_signal_time).total_seconds()
            if elapsed < self._min_signal_interval:
                return None

        # Need enough history
        if len(self._spread_history) < 10:
            return None

        # Don't open new positions if we already have one on this market
        existing_position = self.order_manager.get_position(self.market_id)
        if existing_position:
            return None  # Wait for exit signal to handle it

        # Check for spread opportunity
        signal = self._analyze_spread_opportunity(ob)

        if signal:
            self._last_signal_time = datetime.now()

        return signal

    def _analyze_spread_opportunity(self, ob: OrderBook) -> Optional[Dict[str, Any]]:
        """Analyze if there's a spread opportunity"""
        if not ob.spread_bps or not ob.mid_price:
            return None

        current_spread = float(ob.spread_bps)
        avg_spread = sum(self._spread_history) / len(self._spread_history)
        current_imbalance = float(ob.imbalance) if ob.imbalance else 0

        # Only trade when spread is above average (wide)
        if current_spread < avg_spread * 1.2:
            return None

        # Minimum spread threshold
        if current_spread < float(self.config.min_spread_bps):
            return None

        # Use imbalance to determine direction
        # Positive imbalance = more bids = price likely to go up
        avg_imbalance = sum(self._imbalance_history) / len(self._imbalance_history) if self._imbalance_history else 0

        # Strong imbalance signal - increased threshold to filter noise
        if abs(current_imbalance) < 0.40:  # Need at least 40% imbalance (was 15%)
            return None

        # Determine side
        if current_imbalance > 0.40:
            # More bids - price likely to rise - buy
            side = "buy"
            price = ob.best_bid  # Join the bid
            reason = f"Spread wide ({current_spread:.1f}bps), strong bid imbalance ({current_imbalance:.2f})"
        elif current_imbalance < -0.40:
            # More asks - price likely to fall - sell
            side = "sell"
            price = ob.best_ask  # Join the ask
            reason = f"Spread wide ({current_spread:.1f}bps), strong ask imbalance ({current_imbalance:.2f})"
        else:
            return None

        # Calculate size
        size = self.calculate_position_size(ob.mid_price)

        # Reduce size for scalping - very conservative
        size = size * Decimal("0.3")

        if size <= 0 or price is None:
            return None

        logger.info(f"[{self.name}] Signal: {reason}")

        return {
            "side": side,
            "price": price,
            "size": size,
            "post_only": True,
            "reason": reason
        }

    def _check_exit_conditions(self, ob: OrderBook) -> Optional[Dict[str, Any]]:
        """Check if we should exit current position"""
        position = self.order_manager.get_position(self.market_id)

        if not position:
            return None

        # Check unrealized PnL
        target_profit_usd = position.notional_value * Decimal(str(self.config.target_profit_bps)) / Decimal("10000")

        if position.unrealized_pnl >= target_profit_usd:
            # Take profit
            return {
                "side": "sell" if position.side == OrderSide.BUY else "buy",
                "price": ob.best_bid if position.side == OrderSide.BUY else ob.best_ask,
                "size": position.size,
                "post_only": False,  # Use market order to exit quickly
                "reduce_only": True,
                "reason": f"Take profit: {position.unrealized_pnl:.4f} USD"
            }

        # Stop loss at 2x target
        max_loss = target_profit_usd * 2
        if position.unrealized_pnl <= -max_loss:
            return {
                "side": "sell" if position.side == OrderSide.BUY else "buy",
                "price": ob.best_bid if position.side == OrderSide.BUY else ob.best_ask,
                "size": position.size,
                "post_only": False,
                "reduce_only": True,
                "reason": f"Stop loss: {position.unrealized_pnl:.4f} USD"
            }

        return None

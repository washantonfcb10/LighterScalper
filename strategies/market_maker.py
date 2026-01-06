"""
Market Making Strategy
Places bid/ask orders around mid price to capture spread
Best for low-risk profits with small capital
"""
import logging
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

from strategies.base import BaseStrategy
from src.market_data import OrderBook
from src.order_manager import OrderSide

logger = logging.getLogger(__name__)


class MarketMakerStrategy(BaseStrategy):
    """
    Simple market making strategy:
    - Places limit orders on both sides of the book
    - Uses post-only orders to avoid taker fees
    - Profits from bid-ask spread
    - Conservative for small capital
    """

    def __init__(self, order_manager, trading_config, market_id: int = 0):
        super().__init__(
            name="market_maker",
            order_manager=order_manager,
            trading_config=trading_config,
            market_id=market_id
        )
        self._last_refresh = datetime.now()
        self._bid_order_id: Optional[str] = None
        self._ask_order_id: Optional[str] = None

    async def on_orderbook_update(self, orderbook: OrderBook):
        """Handle orderbook updates"""
        self.update_orderbook(orderbook)

    async def evaluate(self) -> Optional[Dict[str, Any]]:
        """Evaluate if we should place/update market making orders"""
        if not self.is_enabled():
            return None

        if not self._last_orderbook:
            return None

        ob = self._last_orderbook

        # Check if spread is wide enough
        if not ob.spread_bps or ob.spread_bps < self.config.mm_spread_bps / 2:
            logger.debug(f"[{self.name}] Spread too tight: {ob.spread_bps} bps")
            return None

        # Check if we need to refresh orders
        elapsed = (datetime.now() - self._last_refresh).total_seconds()
        if elapsed < self.config.order_refresh_seconds:
            return None

        return await self._manage_orders(ob)

    async def _manage_orders(self, ob: OrderBook) -> Optional[Dict[str, Any]]:
        """Manage bid and ask orders"""
        if not ob.mid_price or not ob.best_bid or not ob.best_ask:
            return None

        # Don't MM if we have a large existing position (risk management)
        existing_position = self.order_manager.get_position(self.market_id)
        if existing_position and existing_position.notional_value > Decimal("50"):
            logger.debug(f"[{self.name}] Skipping MM - existing position too large")
            return None

        mid = ob.mid_price
        half_spread_bps = Decimal(str(self.config.mm_spread_bps)) / 2

        # Calculate our bid and ask prices
        # Place orders inside the spread to get priority
        bid_price = mid * (1 - half_spread_bps / Decimal("10000"))
        ask_price = mid * (1 + half_spread_bps / Decimal("10000"))

        # Round to tick size (assume 0.01 for now)
        tick = Decimal("0.01")
        bid_price = (bid_price / tick).quantize(Decimal("1")) * tick
        ask_price = (ask_price / tick).quantize(Decimal("1")) * tick

        # Calculate size
        size = self.calculate_position_size(mid)

        # Limit size for small capital
        max_size_usd = Decimal(str(self.config.mm_order_size_usd))
        if mid > 0:
            max_size = max_size_usd / mid
            size = min(size, max_size)

        if size <= 0:
            return None

        # Cancel existing orders
        await self.cleanup()

        # Place bid order
        bid_order = await self.order_manager.place_limit_order(
            market_id=self.market_id,
            side=OrderSide.BUY,
            price=bid_price,
            size=size,
            strategy=self.name,
            post_only=True
        )

        if bid_order:
            self._bid_order_id = bid_order.order_id

        # Place ask order
        ask_order = await self.order_manager.place_limit_order(
            market_id=self.market_id,
            side=OrderSide.SELL,
            price=ask_price,
            size=size,
            strategy=self.name,
            post_only=True
        )

        if ask_order:
            self._ask_order_id = ask_order.order_id

        self._last_refresh = datetime.now()

        logger.info(
            f"[{self.name}] Placed MM orders: "
            f"BID {size}@{bid_price} / ASK {size}@{ask_price} "
            f"(spread: {ob.spread_bps:.1f} bps)"
        )

        return {
            "action": "market_make",
            "bid_price": bid_price,
            "ask_price": ask_price,
            "size": size,
            "spread_bps": ob.spread_bps
        }

    async def cleanup(self):
        """Cancel all market making orders"""
        if self._bid_order_id:
            await self.order_manager.cancel_order(self._bid_order_id)
            self._bid_order_id = None

        if self._ask_order_id:
            await self.order_manager.cancel_order(self._ask_order_id)
            self._ask_order_id = None

        await super().cleanup()

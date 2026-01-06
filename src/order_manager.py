"""
Order Manager
Handles order lifecycle, tracking, and execution
"""
import asyncio
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    order_id: str
    market_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    filled_size: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    strategy: str = ""
    is_post_only: bool = True

    @property
    def remaining_size(self) -> Decimal:
        return self.size - self.filled_size

    @property
    def is_active(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]

    @property
    def fill_pct(self) -> Decimal:
        if self.size > 0:
            return (self.filled_size / self.size) * 100
        return Decimal("0")


@dataclass
class Position:
    market_id: int
    side: OrderSide
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    liquidation_price: Optional[Decimal] = None

    @property
    def notional_value(self) -> Decimal:
        return self.size * self.entry_price


class OrderManager:
    """Manages orders and positions"""

    def __init__(self, client):
        self.client = client
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[int, Position] = {}
        self._order_counter = 0

    async def sync_with_exchange(self):
        """Sync orders and positions with exchange"""
        try:
            # Sync positions
            positions_data = await self.client.get_positions()
            for pos_data in positions_data:
                market_id = pos_data.get("market_id", 0)
                # API uses 'position' field, not 'size'
                size_str = pos_data.get("position", pos_data.get("size", "0"))
                size = Decimal(str(size_str))
                # 'sign' field: 1 = long, -1 = short
                sign = pos_data.get("sign", 1)
                if sign == -1:
                    size = -size

                if abs(size) > 0:
                    symbol = pos_data.get("symbol", f"MKT{market_id}")
                    entry_price = Decimal(str(pos_data.get("avg_entry_price", pos_data.get("entry_price", 0))))
                    self.positions[market_id] = Position(
                        market_id=market_id,
                        side=OrderSide.BUY if size > 0 else OrderSide.SELL,
                        size=abs(size),
                        entry_price=entry_price,
                        unrealized_pnl=Decimal(str(pos_data.get("unrealized_pnl", 0))),
                        realized_pnl=Decimal(str(pos_data.get("realized_pnl", 0))),
                        liquidation_price=Decimal(str(pos_data.get("liquidation_price", 0)))
                        if pos_data.get("liquidation_price") and pos_data.get("liquidation_price") != "0" else None
                    )
                    logger.debug(f"Synced position: {symbol} {size} @ {entry_price}")
                elif market_id in self.positions:
                    del self.positions[market_id]

            # Sync open orders
            open_orders = await self.client.get_open_orders()
            exchange_order_ids = set()

            for order_data in open_orders:
                order_id = order_data.get("order_id", "")
                exchange_order_ids.add(order_id)

                if order_id not in self.orders:
                    # New order from exchange
                    self.orders[order_id] = Order(
                        order_id=order_id,
                        market_id=order_data.get("market_id", 0),
                        side=OrderSide.BUY if order_data.get("side", "").lower() == "buy" else OrderSide.SELL,
                        price=Decimal(str(order_data.get("price", 0))),
                        size=Decimal(str(order_data.get("size", 0))),
                        filled_size=Decimal(str(order_data.get("filled_size", 0))),
                        status=OrderStatus.OPEN
                    )
                else:
                    # Update existing order
                    self.orders[order_id].filled_size = Decimal(str(order_data.get("filled_size", 0)))
                    self.orders[order_id].status = OrderStatus.OPEN
                    self.orders[order_id].updated_at = datetime.now()

            # Mark orders not on exchange as filled/cancelled
            for order_id, order in list(self.orders.items()):
                if order.is_active and order_id not in exchange_order_ids:
                    if order.filled_size > 0:
                        order.status = OrderStatus.FILLED
                    else:
                        order.status = OrderStatus.CANCELLED
                    order.updated_at = datetime.now()

            logger.debug(f"Synced {len(self.positions)} positions, {len(open_orders)} open orders")

        except Exception as e:
            logger.error(f"Failed to sync with exchange: {e}")

    async def place_limit_order(
        self,
        market_id: int,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        strategy: str = "",
        post_only: bool = True,
        reduce_only: bool = False
    ) -> Optional[Order]:
        """Place a limit order"""
        try:
            result = await self.client.create_limit_order(
                market_id=market_id,
                side=side.value,
                price=price,
                size=size,
                post_only=post_only,
                reduce_only=reduce_only
            )

            if result:
                order_id = result.get("order_id", f"local_{self._order_counter}")
                self._order_counter += 1

                order = Order(
                    order_id=order_id,
                    market_id=market_id,
                    side=side,
                    price=price,
                    size=size,
                    status=OrderStatus.OPEN,
                    strategy=strategy,
                    is_post_only=post_only
                )
                self.orders[order_id] = order
                logger.info(f"Placed {side.value} order: {size} @ {price} (strategy: {strategy})")
                return order

        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
        return None

    async def place_market_order(
        self,
        market_id: int,
        side: OrderSide,
        size: Decimal,
        strategy: str = "",
        reduce_only: bool = False
    ) -> Optional[Order]:
        """Place a market order"""
        try:
            result = await self.client.create_market_order(
                market_id=market_id,
                side=side.value,
                size=size,
                reduce_only=reduce_only
            )

            if result:
                order_id = result.get("order_id", f"local_{self._order_counter}")
                self._order_counter += 1

                order = Order(
                    order_id=order_id,
                    market_id=market_id,
                    side=side,
                    price=Decimal("0"),  # Market order, price unknown
                    size=size,
                    filled_size=size,  # Assume filled
                    status=OrderStatus.FILLED,
                    strategy=strategy,
                    is_post_only=False
                )
                self.orders[order_id] = order
                logger.info(f"Placed market {side.value} order: {size} (strategy: {strategy})")
                return order

        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
        return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if order_id not in self.orders:
            logger.warning(f"Order {order_id} not found")
            return False

        order = self.orders[order_id]
        if not order.is_active:
            return True

        success = await self.client.cancel_order(order_id, order.market_id)
        if success:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
        return success

    async def cancel_all_orders(self, market_id: Optional[int] = None, strategy: Optional[str] = None):
        """Cancel all orders, optionally filtered by market or strategy"""
        orders_to_cancel = [
            o for o in self.orders.values()
            if o.is_active
            and (market_id is None or o.market_id == market_id)
            and (strategy is None or o.strategy == strategy)
        ]

        for order in orders_to_cancel:
            await self.cancel_order(order.order_id)

        logger.info(f"Cancelled {len(orders_to_cancel)} orders")

    def get_open_orders(self, market_id: Optional[int] = None, strategy: Optional[str] = None) -> List[Order]:
        """Get open orders"""
        return [
            o for o in self.orders.values()
            if o.is_active
            and (market_id is None or o.market_id == market_id)
            and (strategy is None or o.strategy == strategy)
        ]

    def get_position(self, market_id: int) -> Optional[Position]:
        """Get position for a market"""
        return self.positions.get(market_id)

    def get_total_unrealized_pnl(self) -> Decimal:
        """Get total unrealized PnL across all positions"""
        return sum(p.unrealized_pnl for p in self.positions.values())

    def get_total_realized_pnl(self) -> Decimal:
        """Get total realized PnL"""
        return sum(p.realized_pnl for p in self.positions.values())

    def get_total_exposure(self) -> Decimal:
        """Get total notional exposure"""
        return sum(p.notional_value for p in self.positions.values())

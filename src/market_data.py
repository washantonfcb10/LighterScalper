"""
Market Data Handler
Real-time market data processing and analysis
"""
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable
from decimal import Decimal
from dataclasses import dataclass, field
from datetime import datetime
import json

import lighter
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass
class OrderBook:
    market_id: int
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[Decimal]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_bps(self) -> Optional[Decimal]:
        if self.spread and self.mid_price:
            return (self.spread / self.mid_price) * Decimal("10000")
        return None

    @property
    def bid_liquidity(self) -> Decimal:
        return sum(level.size for level in self.bids[:5])

    @property
    def ask_liquidity(self) -> Decimal:
        return sum(level.size for level in self.asks[:5])

    @property
    def imbalance(self) -> Decimal:
        """Order book imbalance: positive = more bids, negative = more asks"""
        total = self.bid_liquidity + self.ask_liquidity
        if total > 0:
            return (self.bid_liquidity - self.ask_liquidity) / total
        return Decimal("0")


@dataclass
class MarketInfo:
    market_id: int
    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    min_order_size: Decimal
    funding_rate: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")
    index_price: Decimal = Decimal("0")
    open_interest: Decimal = Decimal("0")
    volume_24h: Decimal = Decimal("0")


class MarketDataManager:
    """Manages market data from Lighter DEX"""

    def __init__(self, config: Config, client):
        self.config = config
        self.client = client
        self.orderbooks: Dict[int, OrderBook] = {}
        self.markets: Dict[int, MarketInfo] = {}
        self.ws_client: Optional[lighter.WsClient] = None
        self._running = False
        self._callbacks: List[Callable] = []

    async def initialize(self, target_markets: List[int] = None) -> bool:
        """Load initial market data for specific markets only"""
        try:
            # Default to ETH and BTC only
            if target_markets is None:
                target_markets = [0, 1]  # ETH, BTC

            # Get available markets
            markets_data = await self.client.get_markets()
            if markets_data:
                await self._process_markets(markets_data)
                logger.info(f"Loaded {len(self.markets)} markets")

            # Only get orderbooks for target markets
            for market_id in target_markets:
                await self.refresh_orderbook(market_id)
                await asyncio.sleep(0.5)  # Rate limit

            return True
        except Exception as e:
            logger.error(f"Failed to initialize market data: {e}")
            return False

    async def _process_markets(self, markets_data: Any):
        """Process market data into MarketInfo objects"""
        if isinstance(markets_data, dict) and "order_books" in markets_data:
            for market in markets_data["order_books"]:
                market_id = market.get("market_id", 0)
                self.markets[market_id] = MarketInfo(
                    market_id=market_id,
                    symbol=market.get("symbol", f"MARKET_{market_id}"),
                    base_asset=market.get("base_asset", ""),
                    quote_asset=market.get("quote_asset", "USDC"),
                    tick_size=Decimal(str(market.get("tick_size", "0.01"))),
                    min_order_size=Decimal(str(market.get("min_order_size", "0.001"))),
                    funding_rate=Decimal(str(market.get("funding_rate", "0"))),
                    mark_price=Decimal(str(market.get("mark_price", "0"))),
                    index_price=Decimal(str(market.get("index_price", "0"))),
                    open_interest=Decimal(str(market.get("open_interest", "0"))),
                    volume_24h=Decimal(str(market.get("volume_24h", "0")))
                )

    async def refresh_orderbook(self, market_id: int) -> Optional[OrderBook]:
        """Refresh orderbook from REST API"""
        try:
            ob_data = await self.client.get_orderbook_orders(market_id)
            if ob_data:
                self.orderbooks[market_id] = self._parse_orderbook(market_id, ob_data)
                return self.orderbooks[market_id]
        except Exception as e:
            logger.debug(f"Failed to refresh orderbook for market {market_id}: {e}")
        return None

    def _parse_orderbook(self, market_id: int, data: Dict) -> OrderBook:
        """Parse orderbook data into OrderBook object"""
        bids = []
        asks = []

        if "bids" in data:
            for bid in data["bids"][:20]:
                # Use remaining_base_amount as size
                bids.append(OrderBookLevel(
                    price=Decimal(str(bid.get("price", 0))),
                    size=Decimal(str(bid.get("remaining_base_amount", 0)))
                ))

        if "asks" in data:
            for ask in data["asks"][:20]:
                asks.append(OrderBookLevel(
                    price=Decimal(str(ask.get("price", 0))),
                    size=Decimal(str(ask.get("remaining_base_amount", 0)))
                ))

        return OrderBook(
            market_id=market_id,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            last_update=datetime.now()
        )

    async def start_websocket(self, market_ids: List[int]):
        """Start WebSocket connection for real-time updates"""
        try:
            self._running = True

            def on_orderbook_update(data):
                try:
                    market_id = data.get("market_id", 0)
                    self.orderbooks[market_id] = self._parse_orderbook(market_id, data)
                    for callback in self._callbacks:
                        asyncio.create_task(callback(market_id, self.orderbooks[market_id]))
                except Exception as e:
                    logger.error(f"Error processing orderbook update: {e}")

            self.ws_client = lighter.WsClient(
                url=self.config.network.ws_url,
                order_book_market_ids=market_ids,
                on_order_book_update=on_orderbook_update
            )

            logger.info(f"Starting WebSocket for markets: {market_ids}")
            await self.ws_client.run()

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self._running = False

    async def stop_websocket(self):
        """Stop WebSocket connection"""
        self._running = False
        if self.ws_client:
            await self.ws_client.close()

    def add_orderbook_callback(self, callback: Callable):
        """Add callback for orderbook updates"""
        self._callbacks.append(callback)

    def get_orderbook(self, market_id: int) -> Optional[OrderBook]:
        """Get cached orderbook"""
        return self.orderbooks.get(market_id)

    def get_market(self, market_id: int) -> Optional[MarketInfo]:
        """Get market info"""
        return self.markets.get(market_id)

    def get_best_prices(self, market_id: int) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Get best bid and ask prices"""
        ob = self.get_orderbook(market_id)
        if ob:
            return ob.best_bid, ob.best_ask
        return None, None

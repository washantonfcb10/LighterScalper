"""
Lighter DEX Client Wrapper
Handles connection, authentication, and API interactions
"""
import asyncio
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal

import lighter
from config import Config

logger = logging.getLogger(__name__)


class LighterClient:
    """Wrapper around Lighter SDK for trading operations"""

    def __init__(self, config: Config):
        self.config = config
        self.api_client: Optional[lighter.ApiClient] = None
        self.signer_client: Optional[lighter.SignerClient] = None
        self.account_api: Optional[lighter.AccountApi] = None
        self.order_api: Optional[lighter.OrderApi] = None
        self.tx_api: Optional[lighter.TransactionApi] = None
        self._initialized = False
        # Order submission lock to prevent nonce conflicts
        self._order_lock = asyncio.Lock()
        self._last_order_time = 0.0
        self._min_order_interval = 0.5  # Minimum 500ms between orders

    async def connect(self) -> bool:
        """Initialize connection to Lighter DEX"""
        try:
            logger.info(f"Connecting to Lighter DEX at {self.config.network.base_url}")

            # Initialize API client
            self.api_client = lighter.ApiClient(
                lighter.Configuration(host=self.config.network.base_url)
            )

            # Initialize API instances
            self.account_api = lighter.AccountApi(self.api_client)
            self.order_api = lighter.OrderApi(self.api_client)
            self.tx_api = lighter.TransactionApi(self.api_client)

            # Initialize signer client for trading
            if self.config.api_key_private_key:
                self.signer_client = lighter.SignerClient(
                    url=self.config.network.base_url,
                    api_private_keys={self.config.api_key_index: self.config.api_key_private_key},
                    account_index=self.config.account_index
                )
                logger.info("Signer client initialized for trading")

            self._initialized = True
            logger.info("Successfully connected to Lighter DEX")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Lighter DEX: {e}")
            return False

    async def disconnect(self):
        """Clean up connections"""
        if self.api_client:
            await self.api_client.close()
            logger.info("Disconnected from Lighter DEX")

    async def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information including balances and positions"""
        try:
            account = await self.account_api.account(
                by="index",
                value=str(self.config.account_index)
            )
            if account:
                data = account.to_dict()
                # Extract from nested accounts array
                if "accounts" in data and data["accounts"]:
                    return data["accounts"][0]
                return data
            return None
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return None

    async def get_account_balance(self) -> Decimal:
        """Get USDC balance"""
        account = await self.get_account_info()
        if account:
            # Try collateral first, then available_balance
            if "collateral" in account and account["collateral"]:
                return Decimal(str(account["collateral"]))
            if "available_balance" in account and account["available_balance"]:
                return Decimal(str(account["available_balance"]))
        return Decimal("0")

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions"""
        account = await self.get_account_info()
        if account and "positions" in account:
            return account["positions"]
        return []

    async def get_orderbook(self, market_id: int = 0) -> Optional[Dict[str, Any]]:
        """Get orderbook details (market info) for a market"""
        try:
            orderbook = await self.order_api.order_book_details(market_id=market_id)
            return orderbook.to_dict() if orderbook else None
        except Exception as e:
            logger.error(f"Failed to get orderbook: {e}")
            return None

    async def get_orderbook_orders(self, market_id: int = 0, limit: int = 20) -> Optional[Dict[str, Any]]:
        """Get actual orderbook with bids and asks"""
        try:
            orderbook = await self.order_api.order_book_orders(market_id=market_id, limit=limit)
            return orderbook.to_dict() if orderbook else None
        except Exception as e:
            # Suppress rate limit errors (429) - they're expected
            if "429" not in str(e):
                logger.error(f"Failed to get orderbook orders: {e}")
            return None

    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all available markets"""
        try:
            markets = await self.order_api.order_books()
            return markets.to_dict() if markets else []
        except Exception as e:
            logger.error(f"Failed to get markets: {e}")
            return []

    async def get_recent_trades(self, market_id: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent trades for a market"""
        try:
            trades = await self.order_api.recent_trades(market_id=market_id, limit=limit)
            return trades.to_dict() if trades else []
        except Exception as e:
            logger.error(f"Failed to get recent trades: {e}")
            return []

    async def get_exchange_stats(self) -> Optional[Dict[str, Any]]:
        """Get exchange statistics"""
        try:
            stats = await self.order_api.exchange_stats()
            return stats.to_dict() if stats else None
        except Exception as e:
            logger.error(f"Failed to get exchange stats: {e}")
            return None

    async def get_next_nonce(self) -> int:
        """Get next nonce for transaction signing"""
        try:
            nonce_info = await self.tx_api.next_nonce(
                account_index=self.config.account_index,
                api_key_index=self.config.api_key_index
            )
            return nonce_info.next_nonce if nonce_info else 0
        except Exception as e:
            logger.error(f"Failed to get nonce: {e}")
            return 0

    _order_counter = 0  # Class-level order counter

    async def _acquire_order_slot(self):
        """Acquire lock and ensure minimum interval between orders"""
        import time
        async with self._order_lock:
            now = time.time()
            elapsed = now - self._last_order_time
            if elapsed < self._min_order_interval:
                await asyncio.sleep(self._min_order_interval - elapsed)
            self._last_order_time = time.time()

    async def _submit_with_retry(self, order_func, max_retries: int = 3, *args, **kwargs):
        """Submit order with retry logic for nonce errors"""
        last_error = None
        for attempt in range(max_retries):
            try:
                await self._acquire_order_slot()
                return await order_func(*args, **kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "invalid nonce" in error_str.lower() or "21104" in error_str:
                    logger.warning(f"Nonce error (attempt {attempt + 1}/{max_retries}), retrying...")
                    await asyncio.sleep(1.0 * (attempt + 1))  # Exponential backoff
                else:
                    raise  # Re-raise non-nonce errors
        raise last_error  # All retries failed

    # Market-specific decimal places (size_decimals, price_decimals, min_size)
    MARKET_DECIMALS = {
        0: (4, 2, Decimal("0.006")),    # ETH - min ~$20 at $3400
        1: (5, 1, Decimal("0.00025")),  # BTC - min ~$25 at $100k
        2: (3, 3, Decimal("0.1")),      # SOL - min ~$14 at $140
        7: (0, 6, Decimal("20")),       # XRP - whole numbers only, min ~$48 at $2.4
        8: (1, 5, Decimal("1.0")),      # LINK - 1 decimal, min ~$14 at $14
    }

    # Market ID to symbol mapping
    MARKET_SYMBOLS = {
        0: "ETH",
        1: "BTC",
        2: "SOL",
        7: "XRP",
        8: "LINK",
    }

    def _get_market_params(self, market_id: int) -> tuple:
        """Get market parameters: (size_decimals, price_decimals, min_size)"""
        return self.MARKET_DECIMALS.get(market_id, (4, 2, Decimal("0.001")))

    def _get_scales(self, market_id: int) -> tuple:
        """Get scale factors for a market"""
        size_dec, price_dec, _ = self._get_market_params(market_id)
        return Decimal(10**size_dec), Decimal(10**price_dec)

    def _quantize_size(self, size: Decimal, market_id: int) -> Decimal:
        """Quantize size to market's decimal precision and enforce minimum"""
        size_dec, _, min_size = self._get_market_params(market_id)
        # Create quantize format based on decimals (e.g., "1" for 0 decimals, "0.001" for 3)
        if size_dec == 0:
            quantized = size.quantize(Decimal("1"), rounding="ROUND_DOWN")
        else:
            quantize_str = "0." + "0" * size_dec
            quantized = size.quantize(Decimal(quantize_str), rounding="ROUND_DOWN")
        # Enforce minimum
        return max(quantized, min_size)

    def _get_symbol(self, market_id: int) -> str:
        """Get symbol for a market"""
        return self.MARKET_SYMBOLS.get(market_id, f"MKT{market_id}")

    async def create_limit_order(
        self,
        market_id: int,
        side: str,  # "buy" or "sell"
        price: Decimal,
        size: Decimal,
        post_only: bool = True,
        reduce_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Create a limit order with lock and retry"""
        if not self.signer_client:
            logger.error("Signer client not initialized")
            return None

        symbol = self._get_symbol(market_id)

        # Quantize size to market's precision and enforce minimum
        size = self._quantize_size(size, market_id)

        # Convert to integer amounts using market-specific decimals
        base_scale, price_scale = self._get_scales(market_id)
        base_amount_int = int(size * base_scale)
        price_int = int(price * price_scale)

        # is_ask = True for sell, False for buy
        is_ask = side.lower() == "sell"

        # Determine time in force
        time_in_force = (
            lighter.SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY if post_only
            else lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME
        )

        # Retry loop for nonce errors
        for attempt in range(3):
            try:
                # Acquire lock to prevent concurrent order submissions
                async with self._order_lock:
                    import time
                    now = time.time()
                    elapsed = now - self._last_order_time
                    if elapsed < self._min_order_interval:
                        await asyncio.sleep(self._min_order_interval - elapsed)

                    # Generate unique client order index inside lock
                    LighterClient._order_counter += 1
                    client_order_index = LighterClient._order_counter

                    result = await self.signer_client.create_order(
                        market_index=market_id,
                        client_order_index=client_order_index,
                        base_amount=base_amount_int,
                        price=price_int,
                        is_ask=is_ask,
                        order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                        time_in_force=time_in_force,
                        reduce_only=reduce_only,
                        api_key_index=self.config.api_key_index
                    )

                    self._last_order_time = time.time()

                logger.info(f"Created limit order: {side.upper()} {size} {symbol} @ ${price}")
                # result is a tuple: (CreateOrder, RespSendTx, error_msg)
                if result and result[1]:
                    return {"order_id": str(client_order_index), "result": str(result[1])}
                elif result and result[2]:
                    logger.error(f"Order rejected ({symbol}): {result[2]}")
                return None

            except Exception as e:
                error_str = str(e)
                if ("invalid nonce" in error_str.lower() or "21104" in error_str) and attempt < 2:
                    logger.warning(f"Nonce error on limit order (attempt {attempt + 1}/3), retrying...")
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    logger.error(f"Failed to create limit order: {e}")
                    return None

        return None

    async def create_market_order(
        self,
        market_id: int,
        side: str,
        size: Decimal,
        reduce_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Create a market order with lock and retry (critical for closing positions)"""
        if not self.signer_client:
            logger.error("Signer client not initialized")
            return None

        symbol = self._get_symbol(market_id)

        # is_ask = True for sell, False for buy
        is_ask = side.lower() == "sell"

        # Quantize size to market's precision and enforce minimum
        size = self._quantize_size(size, market_id)

        # Convert to integer amounts using market-specific decimals
        base_scale, price_scale = self._get_scales(market_id)
        base_amount_int = int(size * base_scale)

        # For market orders, use extreme price as slippage tolerance (in smallest units)
        avg_execution_price = int(999999 * price_scale) if is_ask else int(1 * price_scale)

        # Retry loop - more retries for market orders since they're often for closing positions
        for attempt in range(5):
            try:
                # Acquire lock to prevent concurrent order submissions
                async with self._order_lock:
                    import time
                    now = time.time()
                    elapsed = now - self._last_order_time
                    if elapsed < self._min_order_interval:
                        await asyncio.sleep(self._min_order_interval - elapsed)

                    # Generate unique client order index inside lock
                    LighterClient._order_counter += 1
                    client_order_index = LighterClient._order_counter

                    result = await self.signer_client.create_market_order(
                        market_index=market_id,
                        client_order_index=client_order_index,
                        base_amount=base_amount_int,
                        avg_execution_price=avg_execution_price,
                        is_ask=is_ask,
                        reduce_only=reduce_only,
                        api_key_index=self.config.api_key_index
                    )

                    self._last_order_time = time.time()

                logger.info(f"Created market order: {side.upper()} {size} {symbol}")
                if result and result[1]:
                    return {"order_id": str(client_order_index), "result": str(result[1])}
                elif result and result[2]:
                    logger.error(f"Market order rejected ({symbol}): {result[2]}")
                return None

            except Exception as e:
                error_str = str(e)
                if ("invalid nonce" in error_str.lower() or "21104" in error_str) and attempt < 4:
                    logger.warning(f"Nonce error on market order (attempt {attempt + 1}/5), retrying...")
                    await asyncio.sleep(1.5 * (attempt + 1))  # Longer backoff for market orders
                else:
                    logger.error(f"Failed to create market order: {e}")
                    return None

        return None

    async def cancel_order(self, order_id: str, market_id: int) -> bool:
        """Cancel an order"""
        if not self.signer_client:
            logger.error("Signer client not initialized")
            return False

        try:
            result = await self.signer_client.cancel_order(
                market_index=market_id,
                order_index=int(order_id),
                api_key_index=self.config.api_key_index
            )
            logger.info(f"Cancelled order: {order_id}")
            return result and result[1] is not None

        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False

    async def cancel_all_orders(self, market_id: int) -> bool:
        """Cancel all orders for a market"""
        if not self.signer_client:
            logger.error("Signer client not initialized")
            return False

        try:
            result = await self.signer_client.cancel_all_orders(
                market_index=market_id,
                api_key_index=self.config.api_key_index
            )
            logger.info(f"Cancelled all orders for market {market_id}")
            return result and result[1] is not None

        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    async def get_open_orders(self, market_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get open orders"""
        account = await self.get_account_info()
        if account and "open_orders" in account:
            orders = account["open_orders"]
            if market_id is not None:
                orders = [o for o in orders if o.get("market_id") == market_id]
            return orders
        return []

"""
Lighter Scalper Bot - Main Entry Point
A low-risk scalping bot for Lighter perpetual DEX
Designed for small capital (~$10 USDC)
"""
import asyncio
import signal
import logging
from decimal import Decimal
from datetime import datetime
from typing import List, Optional

from config import Config
from src.client import LighterClient
from src.market_data import MarketDataManager, OrderBook
from src.order_manager import OrderManager
from strategies import MarketMakerStrategy, SpreadScalperStrategy, MomentumStrategy
from strategies.base import BaseStrategy
from utils.risk import RiskManager
from utils.logger import setup_logging, log_status

logger = logging.getLogger(__name__)


class LighterScalperBot:
    """Main bot orchestrator"""

    def __init__(self, config: Config):
        self.config = config
        self.client: Optional[LighterClient] = None
        self.market_data: Optional[MarketDataManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.strategies: List[BaseStrategy] = []
        self._running = False
        self._shutdown_event = asyncio.Event()
        # Hard stop state tracking
        self._hard_stop_active = False
        self._hard_stop_markets: set = set()  # Markets currently being closed

    async def initialize(self) -> bool:
        """Initialize all components"""
        logger.info("=" * 60)
        logger.info("LIGHTER SCALPER BOT")
        logger.info("=" * 60)
        logger.info(f"Network: {self.config.network.base_url}")
        logger.info(f"Max Position: ${self.config.trading.max_position_usd}")
        logger.info(f"Max Loss: ${self.config.trading.max_loss_usd}")
        logger.info("=" * 60)

        # Initialize client
        self.client = LighterClient(self.config)
        if not await self.client.connect():
            logger.error("Failed to connect to Lighter DEX")
            return False

        # Get initial account info
        account = await self.client.get_account_info()
        if account:
            balance = Decimal(str(account.get("collateral", 0)))
            logger.info(f"Account balance: ${balance:.2f} USDC")
        else:
            logger.warning("Could not fetch account info - check credentials")
            balance = Decimal("10")  # Assume $10 for testing

        # Initialize order manager
        self.order_manager = OrderManager(self.client)
        await self.order_manager.sync_with_exchange()

        # Initialize market data for all markets we'll trade
        target_markets = [0, 1, 2]  # ETH, BTC, SOL (XRP/LINK minimums too high)
        self.market_data = MarketDataManager(self.config, self.client)
        if not await self.market_data.initialize(target_markets):
            logger.error("Failed to initialize market data")
            return False

        # Initialize risk manager
        self.risk_manager = RiskManager(
            self.config.trading,
            initial_capital=balance
        )

        # Initialize strategies for these markets
        await self._init_strategies()

        logger.info(f"Initialized {len(self.strategies)} strategies")
        logger.info("Bot initialization complete")

        return True

    async def _init_strategies(self):
        """Initialize trading strategies"""
        # ETH=0, BTC=1, SOL=2
        # IMPORTANT: Only ONE strategy per market to prevent conflicts!
        # Previously had 8 strategies causing conflicting trades

        # Market Maker on SOL only (most volume, best for MM)
        self.strategies.append(
            MarketMakerStrategy(
                order_manager=self.order_manager,
                trading_config=self.config.trading,
                market_id=2  # SOL
            )
        )

        # Momentum on ETH (good trends)
        self.strategies.append(
            MomentumStrategy(
                order_manager=self.order_manager,
                trading_config=self.config.trading,
                market_id=0  # ETH
            )
        )

        # Spread Scalper on BTC (tightest spreads)
        self.strategies.append(
            SpreadScalperStrategy(
                order_manager=self.order_manager,
                trading_config=self.config.trading,
                market_id=1  # BTC
            )
        )

        logger.info(f"Strategies: {[f'{s.name}(mkt={s.market_id})' for s in self.strategies]}")

    async def run(self):
        """Main bot loop"""
        self._running = True

        logger.info("Starting bot main loop...")

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_shutdown)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        try:
            # Start main loop
            await asyncio.gather(
                self._strategy_loop(),
                self._status_loop(),
                self._sync_loop(),
                self._wait_for_shutdown()
            )
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            await self.shutdown()

    async def _strategy_loop(self):
        """Run strategy evaluations"""
        # Get unique market IDs from strategies
        strategy_markets = set(s.market_id for s in self.strategies)
        logger.info(f"Trading on markets: {strategy_markets}")

        while self._running:
            try:
                # Check risk limits
                if not self.risk_manager.is_trading_allowed():
                    logger.warning(f"Trading stopped: {self.risk_manager.get_stop_reason()}")
                    # SAFETY: Close ALL positions when risk triggers
                    await self._emergency_close_all()
                    await asyncio.sleep(60)
                    continue

                # PAUSE all new trading during hard stop
                if self._hard_stop_active:
                    logger.debug("Trading paused - hard stop in progress")
                    await asyncio.sleep(5)
                    continue

                # Refresh orderbooks only for markets we trade
                for market_id in strategy_markets:
                    try:
                        ob = await self.market_data.refresh_orderbook(market_id)
                        if ob:
                            # Update strategies with new orderbook
                            for strategy in self.strategies:
                                if strategy.market_id == market_id:
                                    await strategy.on_orderbook_update(ob)
                        await asyncio.sleep(1.0)  # Rate limit between markets
                    except Exception as e:
                        logger.debug(f"Error refreshing market {market_id}: {e}")
                        await asyncio.sleep(2.0)  # Extra delay on error

                # Evaluate strategies
                for strategy in self.strategies:
                    if not strategy.is_enabled():
                        continue

                    try:
                        signal = await strategy.evaluate()
                        if signal and "side" in signal:
                            # Check risk before executing
                            price = Decimal(str(signal.get("price", 0)))
                            size = Decimal(str(signal.get("size", 0)))
                            size_usd = price * size

                            can_trade, reason = self.risk_manager.can_open_position(size_usd)
                            if can_trade:
                                await strategy.execute_signal(signal)
                            else:
                                logger.debug(f"Risk check failed: {reason}")

                    except Exception as e:
                        logger.error(f"Strategy {strategy.name} error: {e}")

                await asyncio.sleep(self.config.trading.position_check_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")
                await asyncio.sleep(5)

    async def _status_loop(self):
        """Periodic status logging and safety checks"""
        MAX_LOSS_PER_POSITION = Decimal("-2.0")  # Hard stop: close if losing more than $2
        check_counter = 0

        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds for safety
                check_counter += 1

                # First sync positions to get latest data
                await self.order_manager.sync_with_exchange()

                # SAFETY: Check each position for hard stop loss
                positions_to_close = []
                for pos in list(self.order_manager.positions.values()):
                    # Skip if already trying to close this market
                    if pos.market_id in self._hard_stop_markets:
                        continue

                    if pos.unrealized_pnl < MAX_LOSS_PER_POSITION:
                        positions_to_close.append(pos)

                # Close positions that hit hard stop
                for pos in positions_to_close:
                    symbol = self.client._get_symbol(pos.market_id)
                    logger.warning(f"HARD STOP: {symbol} position losing ${abs(pos.unrealized_pnl):.2f} > $2 limit!")

                    # Mark market as being closed to prevent duplicate attempts
                    self._hard_stop_markets.add(pos.market_id)
                    self._hard_stop_active = True

                    close_side = "sell" if pos.side.value == "buy" else "buy"
                    try:
                        result = await self.client.create_market_order(
                            market_id=pos.market_id,
                            side=close_side,
                            size=pos.size,
                            reduce_only=True
                        )
                        if result:
                            logger.warning(f"Hard stop executed - closing {symbol} position")
                            # Wait and verify close
                            await asyncio.sleep(2)
                            await self.order_manager.sync_with_exchange()

                            # Check if position is actually closed
                            if pos.market_id not in self.order_manager.positions:
                                logger.info(f"VERIFIED: {symbol} position closed successfully")
                                self._hard_stop_markets.discard(pos.market_id)
                            else:
                                remaining = self.order_manager.positions[pos.market_id]
                                logger.warning(f"Position still open: {remaining.size} {symbol}")
                        else:
                            logger.error(f"Hard stop order failed for {symbol}")
                    except Exception as e:
                        logger.error(f"Failed to hard stop {symbol} position: {e}")

                # Clear hard stop flag if no markets pending
                if not self._hard_stop_markets:
                    self._hard_stop_active = False

                # Update metrics
                balance = await self.client.get_account_balance()
                exposure = self.order_manager.get_total_exposure()
                unrealized_pnl = self.order_manager.get_total_unrealized_pnl()
                realized_pnl = self.order_manager.get_total_realized_pnl()

                self.risk_manager.update_metrics(
                    equity=balance + unrealized_pnl,
                    exposure=exposure,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl
                )

                # Log status every 30 seconds (every 3rd check)
                if check_counter % 3 == 0:
                    open_orders = len(self.order_manager.get_open_orders())
                    positions = len(self.order_manager.positions)

                    status_msg = ""
                    if self._hard_stop_active:
                        status_msg = " [HARD STOP ACTIVE]"

                    log_status(
                        equity=float(balance + unrealized_pnl),
                        exposure=float(exposure),
                        unrealized_pnl=float(unrealized_pnl),
                        open_orders=open_orders,
                        positions=positions
                    )
                    if status_msg:
                        logger.warning(status_msg)

                    # Log strategy stats
                    for strategy in self.strategies:
                        stats = strategy.get_stats()
                        if stats["trades"] > 0:
                            logger.debug(
                                f"  {strategy.name}: {stats['trades']} trades, "
                                f"{stats['win_rate']:.1f}% win rate, "
                                f"PnL: ${stats['total_pnl']:.4f}"
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Status loop error: {e}")

    async def _sync_loop(self):
        """Sync with exchange periodically"""
        while self._running:
            try:
                await asyncio.sleep(10)  # Sync every 10 seconds
                await self.order_manager.sync_with_exchange()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")

    async def _emergency_close_all(self):
        """Emergency close all positions - called when risk limits hit"""
        positions = list(self.order_manager.positions.values())
        if not positions:
            return

        logger.warning(f"EMERGENCY: Closing {len(positions)} positions!")
        for pos in positions:
            try:
                close_side = "sell" if pos.side.value == "buy" else "buy"
                await self.client.create_market_order(
                    market_id=pos.market_id,
                    side=close_side,
                    size=pos.size,
                    reduce_only=True
                )
                logger.warning(f"Emergency closed {pos.size} on market {pos.market_id}")
            except Exception as e:
                logger.error(f"Failed to emergency close position: {e}")

    async def _wait_for_shutdown(self):
        """Wait for shutdown signal"""
        await self._shutdown_event.wait()

    def _handle_shutdown(self):
        """Handle shutdown signal"""
        logger.info("Shutdown signal received")
        self._running = False
        self._shutdown_event.set()

    async def shutdown(self):
        """Clean shutdown"""
        logger.info("Shutting down bot...")

        self._running = False

        # Cancel all orders
        for strategy in self.strategies:
            try:
                await strategy.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up {strategy.name}: {e}")

        # Close positions if any
        positions = list(self.order_manager.positions.values())
        if positions:
            logger.warning(f"Closing {len(positions)} open positions...")
            for pos in positions:
                try:
                    side = "sell" if pos.side.value == "buy" else "buy"
                    await self.order_manager.place_market_order(
                        market_id=pos.market_id,
                        side=pos.side,
                        size=pos.size,
                        strategy="shutdown",
                        reduce_only=True
                    )
                except Exception as e:
                    logger.error(f"Error closing position: {e}")

        # Disconnect
        if self.client:
            await self.client.disconnect()

        # Log final stats
        if self.risk_manager:
            status = self.risk_manager.get_status()
            logger.info("=" * 60)
            logger.info("FINAL STATUS")
            logger.info(f"  Equity: ${status['equity']:.2f}")
            logger.info(f"  Realized PnL: ${status['realized_pnl']:.4f}")
            logger.info(f"  Max Drawdown: {status['max_drawdown_pct']:.1f}%")
            logger.info("=" * 60)

        logger.info("Bot shutdown complete")


async def main():
    """Main entry point"""
    # Setup logging
    setup_logging(log_level="INFO", log_to_file=True)

    # Load configuration
    config = Config.from_env()

    # Validate config
    if not config.eth_private_key or not config.api_key_private_key:
        logger.error("Missing credentials. Please set up .env file.")
        logger.error("Copy .env.example to .env and fill in your values.")
        return

    # Create and run bot
    bot = LighterScalperBot(config)

    if await bot.initialize():
        await bot.run()
    else:
        logger.error("Failed to initialize bot")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")

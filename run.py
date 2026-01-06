#!/usr/bin/env python3
"""
Lighter Scalper Bot - Runner Script
Provides easy launching with different modes
"""
import asyncio
import argparse
import logging
from decimal import Decimal

from config import Config
from src.client import LighterClient
from src.market_data import MarketDataManager
from src.order_manager import OrderManager
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


async def test_connection():
    """Test connection to Lighter DEX"""
    setup_logging(log_level="INFO", log_to_file=False)

    config = Config.from_env()

    logger.info("Testing connection to Lighter DEX...")
    logger.info(f"Network: {config.network.base_url}")

    client = LighterClient(config)

    if await client.connect():
        logger.info("Connection successful!")

        # Get account info
        account = await client.get_account_info()
        if account:
            logger.info(f"Account Index: {config.account_index}")
            logger.info(f"Collateral: ${account.get('collateral', 'N/A')} USDC")

            positions = account.get('positions', [])
            if positions:
                logger.info(f"Open Positions: {len(positions)}")
                for pos in positions:
                    logger.info(f"  Market {pos.get('market_id')}: {pos.get('size')} @ {pos.get('entry_price')}")
            else:
                logger.info("No open positions")
        else:
            logger.warning("Could not fetch account info - check API credentials")

        # Get markets
        markets = await client.get_markets()
        if markets and 'order_books' in markets:
            logger.info(f"\nAvailable Markets: {len(markets['order_books'])}")
            for market in markets['order_books'][:5]:
                logger.info(f"  [{market.get('market_id')}] {market.get('symbol', 'Unknown')}")

        # Get orderbook for BTC
        orderbook = await client.get_orderbook(0)
        if orderbook:
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])
            if bids and asks:
                best_bid = bids[0].get('price', 0) if bids else 0
                best_ask = asks[0].get('price', 0) if asks else 0
                logger.info(f"\nBTC-PERP Orderbook:")
                logger.info(f"  Best Bid: ${best_bid}")
                logger.info(f"  Best Ask: ${best_ask}")
                if best_bid and best_ask:
                    spread_bps = (float(best_ask) - float(best_bid)) / float(best_bid) * 10000
                    logger.info(f"  Spread: {spread_bps:.2f} bps")

        await client.disconnect()
        logger.info("\nConnection test complete!")
    else:
        logger.error("Connection failed!")
        logger.error("Please check your .env configuration")


async def dry_run():
    """Dry run - monitor markets without trading"""
    setup_logging(log_level="INFO", log_to_file=True)

    config = Config.from_env()

    logger.info("=" * 60)
    logger.info("DRY RUN MODE - No trades will be executed")
    logger.info("=" * 60)

    client = LighterClient(config)

    if not await client.connect():
        logger.error("Failed to connect")
        return

    # Only monitor ETH (0) and BTC (1)
    target_markets = [0, 1]

    logger.info("Monitoring ETH and BTC markets... (Ctrl+C to stop)")

    try:
        while True:
            for market_id in target_markets:
                ob_data = await client.get_orderbook_orders(market_id)
                if ob_data and ob_data.get('bids') and ob_data.get('asks'):
                    best_bid = Decimal(ob_data['bids'][0]['price'])
                    best_ask = Decimal(ob_data['asks'][0]['price'])
                    mid = (best_bid + best_ask) / 2
                    spread = best_ask - best_bid
                    spread_bps = (spread / mid) * 10000

                    # Calculate imbalance from top 5 levels
                    bid_vol = sum(Decimal(b['remaining_base_amount']) for b in ob_data['bids'][:5])
                    ask_vol = sum(Decimal(a['remaining_base_amount']) for a in ob_data['asks'][:5])
                    total_vol = bid_vol + ask_vol
                    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else Decimal(0)

                    symbol = "ETH" if market_id == 0 else "BTC"

                    logger.info(
                        f"{symbol} | Mid: ${mid:,.2f} | "
                        f"Spread: {spread_bps:.2f}bps | "
                        f"Imbalance: {imbalance:+.2f}"
                    )

                await asyncio.sleep(0.5)  # Small delay between markets

            await asyncio.sleep(3)  # Wait before next cycle

    except KeyboardInterrupt:
        logger.info("Dry run stopped")
    finally:
        await client.disconnect()


async def run_bot():
    """Run the full trading bot"""
    from main import main as run_main
    await run_main()


def main():
    parser = argparse.ArgumentParser(description="Lighter Scalper Bot")
    parser.add_argument(
        "mode",
        choices=["test", "dry-run", "run"],
        help="Mode: test (check connection), dry-run (monitor only), run (live trading)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.mode == "test":
        asyncio.run(test_connection())
    elif args.mode == "dry-run":
        asyncio.run(dry_run())
    elif args.mode == "run":
        print("\n" + "=" * 60)
        print("WARNING: You are about to run the bot in LIVE TRADING mode!")
        print("This will execute real trades with real money.")
        print("=" * 60)
        confirm = input("\nType 'yes' to confirm: ")
        if confirm.lower() == "yes":
            asyncio.run(run_bot())
        else:
            print("Aborted.")


if __name__ == "__main__":
    main()

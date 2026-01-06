"""Emergency script to close ALL orders and positions"""
import asyncio
from config import Config
from src.client import LighterClient

async def main():
    config = Config.from_env()
    client = LighterClient(config)

    print("Connecting to Lighter DEX...")
    await client.connect()

    # Get account info
    account = await client.get_account_info()
    print(f"\n=== ACCOUNT STATUS ===")
    print(f"Balance: ${account.get('collateral', 'N/A')}")

    # Cancel ALL orders on all markets we trade
    markets = [0, 1, 2]  # ETH, BTC, SOL
    print(f"\n=== CANCELLING ALL ORDERS ===")
    for market_id in markets:
        try:
            result = await client.cancel_all_orders(market_id)
            print(f"Market {market_id}: Cancelled all orders - {result}")
            await asyncio.sleep(1)  # Avoid nonce issues
        except Exception as e:
            print(f"Market {market_id}: Error - {e}")

    # Get and close all positions
    print(f"\n=== CLOSING ALL POSITIONS ===")
    positions = account.get('positions', [])
    for pos in positions:
        market_id = pos.get('market_id')
        size_str = pos.get('position', '0')
        size = float(size_str)
        sign = pos.get('sign', 1)
        symbol = pos.get('symbol', f'MKT{market_id}')

        if abs(size) > 0:
            # Determine close side
            close_side = "sell" if sign == 1 else "buy"
            print(f"Closing {symbol}: {size} (side: {close_side})")

            try:
                from decimal import Decimal
                result = await client.create_market_order(
                    market_id=market_id,
                    side=close_side,
                    size=Decimal(str(abs(size))),
                    reduce_only=True
                )
                print(f"  Result: {result}")
                await asyncio.sleep(2)  # Wait between orders
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print(f"{symbol}: No position")

    # Verify final state
    print(f"\n=== FINAL CHECK ===")
    await asyncio.sleep(2)
    account = await client.get_account_info()
    print(f"Final Balance: ${account.get('collateral', 'N/A')}")

    open_orders = await client.get_open_orders()
    print(f"Open Orders: {len(open_orders)}")

    positions = account.get('positions', [])
    open_positions = [p for p in positions if abs(float(p.get('position', 0))) > 0]
    print(f"Open Positions: {len(open_positions)}")

    await client.disconnect()
    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(main())

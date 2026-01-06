# LighterScalper

Multi-strategy automated trading bot for Lighter DEX perpetual markets.

## Features

- **3 Parallel Strategies**: Market maker (SOL), momentum scalper (ETH), spread scalper (BTC)
- **Risk Management**: Position limits, max drawdown monitoring, hard stops, loss cooldowns
- **Small Capital Friendly**: Optimized for ~$100 accounts with conservative 2x leverage

## Supported Markets

ETH (0) | BTC (1) | SOL (2) | XRP (7) | LINK (8)

## Quick Start

1. Clone the repo
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your credentials
4. Run: `python main.py`

## Architecture

- `src/client.py` - Lighter DEX API wrapper
- `src/market_data.py` - Order book & WebSocket handling
- `src/order_manager.py` - Order lifecycle management
- `utils/risk.py` - Risk controls & position sizing
- `strategies/` - Trading strategy implementations

## Tech Stack

Python 3.8+ | lighter-sdk | asyncio | aiohttp | websockets

## Disclaimer

For educational purposes only. Trading perpetuals carries significant risk. Use at your own risk.

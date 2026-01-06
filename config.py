import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional

load_dotenv()


@dataclass
class NetworkConfig:
    base_url: str
    ws_url: str


NETWORKS = {
    "mainnet": NetworkConfig(
        base_url="https://mainnet.zklighter.elliot.ai",
        ws_url="wss://mainnet.zklighter.elliot.ai/stream"
    ),
    "testnet": NetworkConfig(
        base_url="https://testnet.zklighter.elliot.ai",
        ws_url="wss://testnet.zklighter.elliot.ai/stream"
    )
}


@dataclass
class TradingConfig:
    # Capital constraints - for $100 USDC account
    max_position_usd: float = 25.0  # Max position size in USD
    max_loss_usd: float = 10.0  # Max loss before stopping (10% of capital)

    # Position sizing
    default_leverage: float = 2.0  # Very conservative leverage
    max_leverage: float = 3.0  # Hard cap to prevent over-exposure

    # Risk per trade
    risk_per_trade_pct: float = 2.0  # 2% of capital per trade = $2.00

    # Spread scalping params (adjusted for tight spreads on Lighter)
    min_spread_bps: float = 0.5  # Minimum spread to trade (0.005%) - very tight on Lighter
    target_profit_bps: float = 1.0  # Target profit per trade (0.01%)

    # Market making params (wider spread since market is tight)
    mm_spread_bps: float = 2.0  # Spread for market making orders
    mm_order_size_usd: float = 15.0  # Size per order (meets minimum notional)

    # Funding rate arbitrage
    min_funding_rate_bps: float = 5.0  # Min funding rate to trade

    # Timing (conservative to avoid rate limits and order churn)
    order_refresh_seconds: float = 30.0  # Longer refresh to reduce order spam
    position_check_seconds: float = 5.0  # Slightly slower position checks

    # Safety
    max_open_orders: int = 4
    max_consecutive_losses: int = 3
    cooldown_after_loss_seconds: float = 60.0


@dataclass
class Config:
    # Credentials
    eth_private_key: str
    api_key_private_key: str
    api_key_index: int
    account_index: int

    # Network
    network: NetworkConfig

    # Trading
    trading: TradingConfig

    @classmethod
    def from_env(cls) -> "Config":
        network_name = os.getenv("NETWORK", "mainnet")
        network = NETWORKS.get(network_name, NETWORKS["mainnet"])

        return cls(
            eth_private_key=os.getenv("ETH_PRIVATE_KEY", ""),
            api_key_private_key=os.getenv("API_KEY_PRIVATE_KEY", ""),
            api_key_index=int(os.getenv("API_KEY_INDEX", "3")),
            account_index=int(os.getenv("ACCOUNT_INDEX", "1")),
            network=network,
            trading=TradingConfig(
                max_position_usd=float(os.getenv("MAX_POSITION_USD", "5.0")),
                max_loss_usd=float(os.getenv("MAX_LOSS_USD", "1.0")),
            )
        )

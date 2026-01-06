"""
Risk Management Module
Handles position sizing, exposure limits, and safety checks
"""
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass

from config import TradingConfig

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """Current risk state"""
    total_equity: Decimal = Decimal("0")
    total_exposure: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    exposure_pct: Decimal = Decimal("0")


class RiskManager:
    """Manages trading risk"""

    def __init__(self, config: TradingConfig, initial_capital: Decimal = Decimal("10")):
        self.config = config
        self.initial_capital = initial_capital
        self.metrics = RiskMetrics(total_equity=initial_capital)
        self._peak_equity = initial_capital
        self._daily_start_equity = initial_capital
        self._is_stopped = False
        self._stop_reason: Optional[str] = None

    def update_metrics(
        self,
        equity: Decimal,
        exposure: Decimal,
        unrealized_pnl: Decimal,
        realized_pnl: Decimal
    ):
        """Update risk metrics"""
        self.metrics.total_equity = equity
        self.metrics.total_exposure = exposure
        self.metrics.unrealized_pnl = unrealized_pnl
        self.metrics.realized_pnl = realized_pnl
        self.metrics.daily_pnl = equity - self._daily_start_equity

        # Calculate exposure percentage
        if equity > 0:
            self.metrics.exposure_pct = (exposure / equity) * 100

        # Update peak and drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity > 0:
            current_drawdown = (self._peak_equity - equity) / self._peak_equity * 100
            if current_drawdown > self.metrics.max_drawdown:
                self.metrics.max_drawdown = current_drawdown

        # Check for stop conditions
        self._check_stop_conditions()

    def _check_stop_conditions(self):
        """Check if trading should be stopped"""
        # Max loss check
        total_loss = self.initial_capital - self.metrics.total_equity
        max_loss = Decimal(str(self.config.max_loss_usd))

        if total_loss >= max_loss:
            self._is_stopped = True
            self._stop_reason = f"Max loss reached: ${total_loss:.2f} >= ${max_loss:.2f}"
            logger.warning(f"RISK STOP: {self._stop_reason}")

        # Max drawdown check (50% of capital)
        if self.metrics.max_drawdown >= 50:
            self._is_stopped = True
            self._stop_reason = f"Max drawdown reached: {self.metrics.max_drawdown:.1f}%"
            logger.warning(f"RISK STOP: {self._stop_reason}")

    def is_trading_allowed(self) -> bool:
        """Check if trading is allowed"""
        return not self._is_stopped

    def get_stop_reason(self) -> Optional[str]:
        """Get reason for stop if stopped"""
        return self._stop_reason

    def can_open_position(self, size_usd: Decimal) -> tuple[bool, str]:
        """Check if a new position can be opened"""
        if self._is_stopped:
            return False, self._stop_reason or "Trading stopped"

        # Check max position size
        max_pos = Decimal(str(self.config.max_position_usd))
        if size_usd > max_pos:
            return False, f"Position size ${size_usd:.2f} exceeds max ${max_pos:.2f}"

        # Check total exposure
        new_exposure = self.metrics.total_exposure + size_usd
        max_exposure = self.metrics.total_equity * Decimal(str(self.config.max_leverage))

        if new_exposure > max_exposure:
            return False, f"Would exceed max exposure: ${new_exposure:.2f} > ${max_exposure:.2f}"

        return True, "OK"

    def calculate_safe_size(self, price: Decimal, side: str) -> Decimal:
        """Calculate safe position size based on current risk"""
        if self._is_stopped or price <= 0:
            return Decimal("0")

        # Available margin
        available = self.metrics.total_equity - self.metrics.total_exposure

        # Max position in USD
        max_pos_usd = min(
            Decimal(str(self.config.max_position_usd)),
            available * Decimal(str(self.config.default_leverage)) * Decimal("0.5")  # 50% buffer
        )

        # Convert to size
        size = max_pos_usd / price

        # Minimum size check
        min_size = Decimal("0.0001")
        if size < min_size:
            return Decimal("0")

        return size.quantize(Decimal("0.0001"))

    def get_status(self) -> Dict[str, Any]:
        """Get risk status"""
        return {
            "trading_allowed": self.is_trading_allowed(),
            "stop_reason": self._stop_reason,
            "equity": float(self.metrics.total_equity),
            "exposure": float(self.metrics.total_exposure),
            "exposure_pct": float(self.metrics.exposure_pct),
            "unrealized_pnl": float(self.metrics.unrealized_pnl),
            "realized_pnl": float(self.metrics.realized_pnl),
            "daily_pnl": float(self.metrics.daily_pnl),
            "max_drawdown_pct": float(self.metrics.max_drawdown)
        }

    def reset_daily_stats(self):
        """Reset daily statistics"""
        self._daily_start_equity = self.metrics.total_equity
        logger.info("Daily stats reset")

    def force_stop(self, reason: str):
        """Force stop trading"""
        self._is_stopped = True
        self._stop_reason = reason
        logger.warning(f"FORCE STOP: {reason}")

    def resume_trading(self):
        """Resume trading after manual review"""
        if self._is_stopped:
            logger.info(f"Resuming trading (was stopped: {self._stop_reason})")
            self._is_stopped = False
            self._stop_reason = None

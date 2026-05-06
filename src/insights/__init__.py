"""Coleta de insights da Graph API + reports diário/mensal via Telegram."""
from .collector import collect_now
from .reporter import maybe_daily_report, maybe_monthly_report

__all__ = ["collect_now", "maybe_daily_report", "maybe_monthly_report"]

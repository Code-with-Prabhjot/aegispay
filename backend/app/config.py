"""
AegisPay Configuration Module
Production-grade Pydantic v2 Settings for PayPal Escrow Engine and Settlement.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # PayPal API Credentials and Endpoints
    PAYPAL_CLIENT_ID: str = Field(
        default="",
        description="PayPal REST API Client ID (Sandbox or Live)",
    )
    PAYPAL_CLIENT_SECRET: str = Field(
        default="",
        description="PayPal REST API Client Secret",
    )
    PAYPAL_WEBHOOK_ID: str = Field(
        default="",
        description="PayPal Webhook ID for signature verification",
    )
    PAYPAL_MODE: str = Field(
        default="sandbox",
        description="PayPal operational mode: 'mock' (offline), 'sandbox' or 'live'",
    )

    # Database connection string
    DATABASE_URL: str = Field(
        default="sqlite:///./aegispay.db",
        description="Database URL (sqlite: for dev/tests; schema in backend/db/schema.sql is Postgres-compatible)",
    )

    # Fee Structure & Limits (in integer micro-dollars or basis points)
    PROTOCOL_FEE_BPS: int = Field(
        default=500,
        description="Protocol clearing fee in basis points (500 bps = 5.00%)",
        ge=0,
        le=10_000,
    )
    PAYPAL_PAYOUT_FEE_MICROS: int = Field(
        default=250_000,
        description="PayPal Batch Payout rail fee in micro-dollars (250,000 micros = $0.25 USD)",
        ge=0,
    )
    DAILY_SPEND_LIMIT_MICROS: int = Field(
        default=50_000_000,
        description="Autonomous agent daily spend cap in micro-dollars (50,000,000 micros = $50.00 USD)",
        gt=0,
    )

    # Dedicated Sandbox Accounts (Topology Fix #4)
    BUYER_SANDBOX_EMAIL: str = Field(
        default="buyer-agent-sandbox@personal.example.com",
        description="Sandbox email for buyer agent hold authorizations",
    )
    CLEARINGHOUSE_SANDBOX_EMAIL: str = Field(
        default="aegispay-clearinghouse@business.example.com",
        description="Pre-funded merchant business sandbox account for batch payouts",
    )
    WORKER_SANDBOX_EMAIL: str = Field(
        default="worker-agent-alpha@personal.example.com",
        description="Receiver account for verified worker payouts",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def is_sandbox(self) -> bool:
        """Determines whether the engine should run against the PayPal Sandbox API."""
        return self.PAYPAL_MODE.strip().lower() == "sandbox"

    @property
    def is_mock(self) -> bool:
        """True when PAYPAL_MODE=mock (offline PayPal simulation)."""
        return self.PAYPAL_MODE.strip().lower() == "mock"

    @property
    def paypal_base_url(self) -> str:
        """Returns the appropriate PayPal REST API base URL."""
        return (
            "https://api-m.sandbox.paypal.com"
            if (self.is_sandbox or self.is_mock)
            else "https://api-m.paypal.com"
        )


settings = Settings()

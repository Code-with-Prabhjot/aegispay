"""
AegisPay Configuration Module
Production-grade Pydantic v2 Settings for PayPal Escrow Engine and Settlement.
"""

from pydantic import Field, model_validator
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
    PAYPAL_WEBHOOK_MOCK_BYPASS: bool = Field(
        default=False,
        description="Skip webhook signature verification (local pytest runs only; rejected in live mode)",
    )

    # API access control and trust policy
    API_AUTH_KEY: str = Field(
        default="",
        description="Shared secret required in the X-API-Key header on every non-webhook route. "
        "Optional in mock mode; required in sandbox and live mode.",
    )
    ALLOW_UNBOUND_TEST_SUITE: bool = Field(
        default=False,
        description="Let the submitter supply the test suite when the agreement has none committed "
        "(always allowed in mock mode; never enable for real funds)",
    )
    ALLOW_UNISOLATED_SANDBOX: bool = Field(
        default=False,
        description="Run untrusted code without Docker isolation (always allowed in mock mode; "
        "never enable for real funds)",
    )
    REQUIRE_A2A_SIGNATURES: bool = Field(
        default=False,
        description="Reject contracts that do not carry valid buyer and worker Ed25519 signatures",
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000",
        description="Comma-separated browser origins allowed by CORS and the WebSocket Origin check "
        "(add the deployed dashboard's origin, e.g. https://aegispay.example.com)",
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

    # Arbiter sandbox and semantic judge
    SANDBOX_MODE: str = Field(
        default="auto",
        description="Test execution backend: 'docker', 'subprocess' or 'auto'",
    )
    SANDBOX_TEST_TIMEOUT_SECONDS: float = Field(
        default=15.0,
        gt=0,
        le=300,
        description="Time the buyer's tests may run, counted after pytest has started "
        "(interpreter and container startup are not charged to the worker)",
    )
    SANDBOX_IMAGE: str = Field(
        default="aegispay-sandbox-runner:latest",
        description="Docker image used by the hardened sandbox runner",
    )
    LLM_JUDGE_ENABLED: bool = Field(
        default=False,
        description="Use LLM semantic judge (Claude / Gemini)",
    )
    LLM_JUDGE_MODEL: str = Field(default="claude-haiku-5-5", description="Claude model id for the judge")
    GEMINI_JUDGE_MODEL: str = Field(default="gemini-1.5-flash", description="Gemini model id for the judge")
    LLM_JUDGE_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, description="Judge call timeout")
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key (optional)")
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key (optional)")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def reject_bypass_outside_test_modes(self) -> "Settings":
        # Anything other than these three used to fall through to the LIVE PayPal API.
        if self.PAYPAL_MODE.strip().lower() not in ("mock", "sandbox", "live"):
            raise ValueError(
                f"PAYPAL_MODE must be 'mock', 'sandbox' or 'live', got {self.PAYPAL_MODE!r}"
            )
        if self.PAYPAL_WEBHOOK_MOCK_BYPASS and not (self.is_sandbox or self.is_mock):
            raise ValueError("PAYPAL_WEBHOOK_MOCK_BYPASS is only allowed with PAYPAL_MODE=mock or sandbox")
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, without trailing slashes."""
        return [o.strip().rstrip("/") for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def allow_unbound_test_suite(self) -> bool:
        """True when a submitter-supplied test suite may be graded (demo and test runs only)."""
        return self.ALLOW_UNBOUND_TEST_SUITE or self.is_mock

    @property
    def allow_unisolated_sandbox(self) -> bool:
        """True when untrusted code may run outside Docker (demo and test runs only)."""
        return self.ALLOW_UNISOLATED_SANDBOX or self.is_mock

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

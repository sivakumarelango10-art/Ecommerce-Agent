from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.db.models import Address, CartItem


class PolicyConfigurationError(RuntimeError):
    """Base exception raised when required store policy configuration is missing or invalid."""


class ShippingConfigurationError(PolicyConfigurationError):
    """Raised when shipping calculation policy is not configured or invalid."""


class TaxConfigurationError(PolicyConfigurationError):
    """Raised when tax calculation policy is not configured or invalid."""


class ShippingPolicyProtocol(Protocol):
    def calculate_shipping_fee(
        self,
        subtotal: Decimal,
        items: list[CartItem],
        shipping_address: Address,
        settings: Settings,
    ) -> Decimal:
        """Calculate applicable shipping fee. Must raise ShippingConfigurationError if unconfigured."""
        ...


class TaxPolicyProtocol(Protocol):
    def calculate_tax(
        self,
        subtotal: Decimal,
        shipping_fee: Decimal,
        items: list[CartItem],
        shipping_address: Address,
        settings: Settings,
    ) -> Decimal:
        """Calculate applicable tax. Must raise TaxConfigurationError if unconfigured."""
        ...


class ConfigurableFlatShippingPolicy:
    """Configurable flat-rate shipping policy."""

    def __init__(self, flat_fee: Decimal) -> None:
        self.flat_fee = Decimal(str(flat_fee)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def calculate_shipping_fee(
        self,
        subtotal: Decimal,
        items: list[CartItem],
        shipping_address: Address,
        settings: Settings,
    ) -> Decimal:
        return self.flat_fee


class ConfigurablePercentageTaxPolicy:
    """Configurable percentage-based tax policy applied to (subtotal + shipping_fee)."""

    def __init__(self, tax_rate_percentage: Decimal) -> None:
        self.tax_rate = Decimal(str(tax_rate_percentage))

    def calculate_tax(
        self,
        subtotal: Decimal,
        shipping_fee: Decimal,
        items: list[CartItem],
        shipping_address: Address,
        settings: Settings,
    ) -> Decimal:
        taxable_base = subtotal + shipping_fee
        tax = taxable_base * (self.tax_rate / Decimal("100"))
        return tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Policy Registry for runtime dependency injection and configuration
_shipping_policy: ShippingPolicyProtocol | None = None
_tax_policy: TaxPolicyProtocol | None = None


def set_shipping_policy(policy: ShippingPolicyProtocol | None) -> None:
    """Register the active shipping policy engine."""
    global _shipping_policy
    _shipping_policy = policy


def set_tax_policy(policy: TaxPolicyProtocol | None) -> None:
    """Register the active tax policy engine."""
    global _tax_policy
    _tax_policy = policy


def get_shipping_policy(settings: Settings) -> ShippingPolicyProtocol:
    """Retrieve the configured shipping policy engine.

    Raises ShippingConfigurationError if unconfigured.
    """
    if _shipping_policy is None:
        raise ShippingConfigurationError(
            "Shipping calculation policy is not configured."
        )
    return _shipping_policy


def get_tax_policy(settings: Settings) -> TaxPolicyProtocol:
    """Retrieve the configured tax policy engine.

    Raises TaxConfigurationError if unconfigured.
    """
    if _tax_policy is None:
        raise TaxConfigurationError("Tax calculation policy is not configured.")
    return _tax_policy


def validate_policy_amount(amount: object, policy_name: str) -> Decimal:
    """Validate that calculated policy amount is a non-negative, finite Decimal."""
    if not isinstance(amount, Decimal):
        raise PolicyConfigurationError(
            f"{policy_name} calculation must return a Decimal, got {type(amount).__name__}."
        )
    if amount.is_nan() or amount.is_infinite():
        raise PolicyConfigurationError(
            f"{policy_name} calculation returned non-finite value: {amount}."
        )
    if amount < Decimal("0.00"):
        raise PolicyConfigurationError(
            f"{policy_name} calculation cannot return a negative amount: {amount}."
        )
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


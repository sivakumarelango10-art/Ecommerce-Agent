from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.constants import DEFAULT_CATALOGUE_CURRENCY
from backend.app.schemas.catalog import ProductCategoryResponse


class SortOrder(str, Enum):
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"
    NEWEST = "newest"


class ShoppingIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keywords: list[str] = Field(
        default_factory=list,
        description="Extracted search terms or product keywords.",
    )
    product_name: str | None = Field(
        default=None,
        description="Specific product name if mentioned.",
    )
    category: str | None = Field(
        default=None,
        description="Category name or slug if mentioned.",
    )
    min_budget: Decimal | None = Field(
        default=None,
        ge=0,
        description="Minimum price or budget limit.",
    )
    max_budget: Decimal | None = Field(
        default=None,
        ge=0,
        description="Maximum price or budget limit.",
    )
    currency: str | None = Field(
        default=None,
        description="Currency code, for example INR.",
    )
    brand: str | None = Field(
        default=None,
        description="Preferred brand name if mentioned.",
    )
    required_features: list[str] = Field(
        default_factory=list,
        description="Specific features, attributes, or specs requested.",
    )
    use_case: str | None = Field(
        default=None,
        description="Intended use case, e.g. gaming, running, office.",
    )
    in_stock_only: bool | None = Field(
        default=None,
        description="Whether only in-stock items should be matched.",
    )
    min_quantity: int | None = Field(
        default=None,
        ge=1,
        description="Minimum inventory units required.",
    )
    sort_by: SortOrder | None = Field(
        default=None,
        description="Sorting preference.",
    )
    is_shopping_intent: bool = Field(
        default=True,
        description="True if the user's message is a shopping request.",
    )
    is_ambiguous_or_insufficient: bool = Field(
        default=False,
        description="True if the request lacks enough detail to perform a search.",
    )
    clarification_message: str | None = Field(
        default=None,
        description="Question to ask user if the request is ambiguous or insufficient.",
    )


class ShoppingSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: Annotated[str | None, Field(default=None, max_length=1000)] = None
    category: Annotated[str | None, Field(default=None, max_length=120)] = None
    keywords: list[str] = Field(default_factory=list)
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    currency: Annotated[str | None, Field(default=None, max_length=10)] = None
    brand: Annotated[str | None, Field(default=None, max_length=100)] = None
    in_stock_only: bool | None = None
    min_quantity: Annotated[int | None, Field(default=None, ge=1)] = None
    sort_by: SortOrder | None = None
    limit: Annotated[int, Field(default=20, ge=1, le=100)] = 20
    offset: Annotated[int, Field(default=0, ge=0)] = 0

    @model_validator(mode="after")
    def validate_budget_range(self) -> "ShoppingSearchRequest":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.max_price < self.min_price
        ):
            raise ValueError("max_price must be greater than or equal to min_price")
        return self


class ShoppingProductItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    name: str
    description: str | None
    price: Decimal = Field(max_digits=12, decimal_places=2)
    currency: str = DEFAULT_CATALOGUE_CURRENCY
    categories: list[ProductCategoryResponse]
    in_stock: bool
    available_quantity: int = Field(ge=0)
    recommendation_reason: str | None = None


class ShoppingSearchResponse(BaseModel):
    query: str | None = None
    intent: ShoppingIntent | None = None
    currency: str = DEFAULT_CATALOGUE_CURRENCY
    status: Literal[
        "success", "no_results", "insufficient_information", "unsupported_currency"
    ] = "success"
    message: str
    products: list[ShoppingProductItem] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class SupervisorRouteResponse(BaseModel):
    destination: Literal["shopping_agent", "general_assistant"]
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    reason: str
    shopping_response: ShoppingSearchResponse | None = None
    general_answer: str | None = None

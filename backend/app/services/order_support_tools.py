import logging
from typing import Any
from uuid import UUID

from google.genai import types
from sqlalchemy.orm import Session

from backend.app.services.order_service import (
    format_customer_order_detail,
    format_customer_order_status,
    get_customer_order_by_number,
    get_customer_order_items,
    get_customer_order_status,
    list_customer_orders,
)

logger = logging.getLogger(__name__)

SUPPORTED_ORDER_TOOLS = {
    "list_my_orders",
    "get_my_order",
    "get_order_status",
    "get_order_items",
}


def _validate_order_number(order_number: Any) -> tuple[bool, str]:
    """Validate customer order number input format."""
    if not isinstance(order_number, str):
        return False, ""
    cleaned = order_number.strip()
    if not cleaned or len(cleaned) > 64:
        return False, ""
    return True, cleaned


def list_my_orders(
    session: Session,
    current_user_id: UUID,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Retrieve recent orders owned strictly by the authenticated customer."""
    if not isinstance(current_user_id, UUID):
        return {
            "success": False,
            "error": "UNAUTHORIZED",
            "message": "Authentication context is required to access orders.",
        }

    clamped_limit = max(1, min(limit, 50))
    clamped_offset = max(0, offset)
    orders = list_customer_orders(
        session, current_user_id, limit=clamped_limit, offset=clamped_offset
    )
    orders_data = [
        format_customer_order_status(o).model_dump(mode="json")
        for o in orders
    ]
    return {
        "success": True,
        "orders": orders_data,
        "total_returned": len(orders_data),
        "limit": clamped_limit,
        "offset": clamped_offset,
    }


def get_my_order(
    session: Session,
    current_user_id: UUID,
    order_number: str,
) -> dict[str, Any]:
    """Retrieve full customer-facing details of an individual owned order."""
    if not isinstance(current_user_id, UUID):
        return {
            "success": False,
            "error": "UNAUTHORIZED",
            "message": "Authentication context is required to access orders.",
        }

    valid, clean_num = _validate_order_number(order_number)
    if not valid:
        return {
            "success": False,
            "found": False,
            "error": "INVALID_ORDER_NUMBER",
            "message": "A valid order number is required.",
        }

    order = get_customer_order_by_number(session, current_user_id, clean_num)
    if order is None:
        return {
            "success": True,
            "found": False,
            "message": f"Order '{clean_num}' not found.",
        }

    return {
        "success": True,
        "found": True,
        "order": format_customer_order_detail(order).model_dump(mode="json"),
    }


def get_order_status(
    session: Session,
    current_user_id: UUID,
    order_number: str,
) -> dict[str, Any]:
    """Retrieve lightweight status and timing for a customer-owned order."""
    if not isinstance(current_user_id, UUID):
        return {
            "success": False,
            "error": "UNAUTHORIZED",
            "message": "Authentication context is required to access orders.",
        }

    valid, clean_num = _validate_order_number(order_number)
    if not valid:
        return {
            "success": False,
            "found": False,
            "error": "INVALID_ORDER_NUMBER",
            "message": "A valid order number is required.",
        }

    status_summary = get_customer_order_status(session, current_user_id, clean_num)
    if status_summary is None:
        return {
            "success": True,
            "found": False,
            "message": f"Order '{clean_num}' not found.",
        }

    res: dict[str, Any] = {"success": True, "found": True}
    res.update(status_summary.model_dump(mode="json"))
    return res


def get_order_items(
    session: Session,
    current_user_id: UUID,
    order_number: str,
) -> dict[str, Any]:
    """Retrieve purchased items for a customer-owned order."""
    if not isinstance(current_user_id, UUID):
        return {
            "success": False,
            "error": "UNAUTHORIZED",
            "message": "Authentication context is required to access orders.",
        }

    valid, clean_num = _validate_order_number(order_number)
    if not valid:
        return {
            "success": False,
            "found": False,
            "error": "INVALID_ORDER_NUMBER",
            "message": "A valid order number is required.",
        }

    items = get_customer_order_items(session, current_user_id, clean_num)
    if items is None:
        return {
            "success": True,
            "found": False,
            "message": f"Order '{clean_num}' not found.",
        }

    items_data = [item.model_dump(mode="json") for item in items]
    return {
        "success": True,
        "found": True,
        "order_number": clean_num.upper(),
        "items": items_data,
        "total_items": len(items_data),
    }


class OrderSupportTools:
    """Class wrapper binding current authenticated customer context to tool calls."""

    def __init__(self, session: Session, current_user_id: UUID) -> None:
        if not isinstance(current_user_id, UUID):
            raise ValueError("current_user_id must be a valid UUID")
        self.session = session
        self.current_user_id = current_user_id

    def list_my_orders(self, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        return list_my_orders(
            self.session, self.current_user_id, limit=limit, offset=offset
        )

    def get_my_order(self, order_number: str) -> dict[str, Any]:
        return get_my_order(
            self.session, self.current_user_id, order_number=order_number
        )

    def get_order_status(self, order_number: str) -> dict[str, Any]:
        return get_order_status(
            self.session, self.current_user_id, order_number=order_number
        )

    def get_order_items(self, order_number: str) -> dict[str, Any]:
        return get_order_items(
            self.session, self.current_user_id, order_number=order_number
        )


def execute_order_support_tool(
    name: str,
    args: dict[str, Any],
    session: Session,
    current_user_id: UUID,
) -> dict[str, Any]:
    """Execute an agent order tool call with backend-enforced customer isolation.

    Any caller-injected 'user_id', 'customer_id', or unauthorized context is strictly
    discarded; queries execute exclusively under current_user_id.
    """
    if not isinstance(current_user_id, UUID):
        return {
            "success": False,
            "error": "UNAUTHORIZED",
            "message": "Authentication context is required to access orders.",
        }

    if name not in SUPPORTED_ORDER_TOOLS:
        return {
            "success": False,
            "error": "UNKNOWN_TOOL",
            "message": f"Tool '{name}' is not supported.",
        }

    # Defensively strip any attempt to supply customer IDs through tool arguments
    safe_args = {
        k: v for k, v in args.items()
        if k not in ("user_id", "current_user_id", "customer_id")
    }

    if name == "list_my_orders":
        raw_limit = safe_args.get("limit", 10)
        raw_offset = safe_args.get("offset", 0)
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = 10
        try:
            offset = int(raw_offset)
        except (ValueError, TypeError):
            offset = 0
        return list_my_orders(session, current_user_id, limit=limit, offset=offset)

    elif name == "get_my_order":
        order_number = safe_args.get("order_number", "")
        return get_my_order(session, current_user_id, order_number=str(order_number))

    elif name == "get_order_status":
        order_number = safe_args.get("order_number", "")
        return get_order_status(session, current_user_id, order_number=str(order_number))

    elif name == "get_order_items":
        order_number = safe_args.get("order_number", "")
        return get_order_items(session, current_user_id, order_number=str(order_number))

    return {
        "success": False,
        "error": "UNKNOWN_TOOL",
        "message": f"Tool '{name}' is not supported.",
    }


ORDER_SUPPORT_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="list_my_orders",
        description="Retrieve recent orders placed by the currently authenticated customer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "limit": types.Schema(
                    type=types.Type.INTEGER,
                    description="Maximum number of orders to return (1 to 50, default 10).",
                ),
                "offset": types.Schema(
                    type=types.Type.INTEGER,
                    description="Number of orders to skip for pagination (default 0).",
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_my_order",
        description="Retrieve complete details (items, delivery address, financial totals, status) of an order by order number.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "order_number": types.Schema(
                    type=types.Type.STRING,
                    description="The order number (e.g., ORD-20261121-1A2B3C4D).",
                ),
            },
            required=["order_number"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_order_status",
        description="Check current delivery or processing status and amount of a specific order by order number.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "order_number": types.Schema(
                    type=types.Type.STRING,
                    description="The order number to check status for.",
                ),
            },
            required=["order_number"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_order_items",
        description="List all purchased products, SKU codes, quantities, and prices for a specific order by order number.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "order_number": types.Schema(
                    type=types.Type.STRING,
                    description="The order number to list items for.",
                ),
            },
            required=["order_number"],
        ),
    ),
]

ORDER_SUPPORT_TOOL = types.Tool(function_declarations=ORDER_SUPPORT_TOOL_DECLARATIONS)

import logging
from typing import Any
from uuid import UUID

from google.genai import types
from sqlalchemy.orm import Session

from backend.app.services.gemini_service import (
    GeminiProviderError,
    GeminiResponseError,
    GeminiService,
    GeminiTimeoutError,
)
from backend.app.services.order_support_tools import (
    ORDER_SUPPORT_TOOL,
    SUPPORTED_ORDER_TOOLS,
    execute_order_support_tool,
)

logger = logging.getLogger(__name__)

ORDER_SUPPORT_SYSTEM_INSTRUCTION = (
    "You are an authenticated customer order-support assistant for an e-commerce platform. "
    "You have access to tools that retrieve the authenticated customer's own order information only.\n\n"
    "CRITICAL RULES:\n"
    "1. Help the customer check their order status, tracking, order history, purchased items, and details.\n"
    "2. Always rely strictly on actual tool results. Never invent, guess, or hallucinate order numbers, prices, dates, products, tracking information, or order statuses.\n"
    "3. Never claim an operation succeeded without a corresponding tool result.\n"
    "4. You CANNOT cancel, refund, modify, exchange, or update orders or shipping addresses, and you cannot process payments. "
    "If the customer asks to cancel, refund, modify, or return an order, politely explain that orders cannot be modified or cancelled through chat, and direct them to contact customer support.\n"
    "5. If an order is not found, state clearly that the order was not found under their account.\n"
    "6. Never reveal internal identifiers, database IDs, JWTs, tokens, secrets, OTPs, idempotency keys, or technical database information."
)

UNAUTHENTICATED_ORDER_MESSAGE = (
    "To view or check your order details and status, please sign in to your account. "
    "If you placed your order as a guest, please visit our Guest Order Verification "
    "portal with your order number and email address to receive a secure access link."
)

ORDER_SERVICE_UNAVAILABLE_MESSAGE = (
    "Our order support service is temporarily unavailable. Please try again later."
)

MAX_TOOL_TURNS = 3
MAX_TOTAL_FUNCTION_CALLS = 6


class OrderSupportAgent:
    """Agent assisting authenticated customers with order status, tracking, and item inquiries."""

    def __init__(self, gemini_service: GeminiService) -> None:
        self._gemini = gemini_service

    def handle_inquiry(
        self,
        message: str,
        session: Session | None,
        current_user_id: UUID | None,
    ) -> str:
        """Handle customer order question with backend-isolated identity injection and bounded tool calling."""
        clean_msg = message.strip()
        if not clean_msg:
            return "Please provide an order number or question about your orders."

        # Unauthenticated users receive guidance immediately without tool or DB access
        if current_user_id is None:
            return UNAUTHENTICATED_ORDER_MESSAGE

        if session is None:
            logger.error("OrderSupportAgent invoked without active database session")
            return ORDER_SERVICE_UNAVAILABLE_MESSAGE

        # Initialize conversation contents
        history: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=clean_msg)],
            )
        ]

        turn_count = 0
        total_function_calls = 0

        while turn_count < MAX_TOOL_TURNS:
            try:
                result = self._gemini.generate(
                    history,
                    tools=[ORDER_SUPPORT_TOOL],
                    system_instruction=ORDER_SUPPORT_SYSTEM_INSTRUCTION,
                )
            except (GeminiTimeoutError, GeminiProviderError, GeminiResponseError) as exc:
                logger.warning(
                    "Gemini request failed in order support agent",
                    extra={"event": "order_support_gemini_error", "error_type": type(exc).__name__},
                )
                raise

            # If model returned direct answer without function calls
            if not result.function_calls:
                if result.text:
                    return result.text
                return "I was unable to retrieve your order information. Please try again."

            # Guard against exceeding total function call quota
            if total_function_calls + len(result.function_calls) > MAX_TOTAL_FUNCTION_CALLS:
                logger.warning(
                    "Order support agent exceeded maximum function call limit",
                    extra={
                        "event": "order_support_function_limit_exceeded",
                        "total_calls": total_function_calls + len(result.function_calls),
                        "limit": MAX_TOTAL_FUNCTION_CALLS,
                    },
                )
                break

            # Append model turn to conversation history
            if result.candidate_content:
                history.append(result.candidate_content)
            else:
                history.append(
                    types.Content(
                        role="model",
                        parts=[
                            types.Part.from_function_call(
                                name=fc.name,
                                args=dict(fc.args or {}),
                            )
                            for fc in result.function_calls
                        ],
                    )
                )

            # Execute tool calls with strict backend identity injection
            response_parts: list[types.Part] = []
            for fc in result.function_calls:
                total_function_calls += 1
                tool_name = fc.name
                raw_args = dict(fc.args or {})

                if tool_name not in SUPPORTED_ORDER_TOOLS:
                    logger.warning(
                        "Model requested unsupported order tool",
                        extra={"event": "unsupported_order_tool", "tool_name": tool_name},
                    )
                    tool_output: dict[str, Any] = {
                        "success": False,
                        "error": "UNKNOWN_TOOL",
                        "message": f"Tool '{tool_name}' is not supported.",
                    }
                else:
                    try:
                        tool_output = execute_order_support_tool(
                            name=tool_name,
                            args=raw_args,
                            session=session,
                            current_user_id=current_user_id,
                        )
                    except Exception as exc:
                        logger.error(
                            "Order support tool execution failed",
                            extra={"event": "order_support_tool_error", "tool": tool_name, "error": type(exc).__name__},
                        )
                        tool_output = {
                            "success": False,
                            "error": "TOOL_ERROR",
                            "message": "Failed to retrieve order data.",
                        }

                response_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": tool_output},
                    )
                )

            # Append tool responses as user turn
            history.append(types.Content(role="user", parts=response_parts))
            turn_count += 1

        # If turn limit was reached without final text, request a final summary
        try:
            summary_history = list(history) + [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text="Based strictly on the tool results above, provide a clear, concise answer to the customer's question."
                        )
                    ],
                )
            ]
            final_result = self._gemini.generate(
                summary_history,
                system_instruction=ORDER_SUPPORT_SYSTEM_INSTRUCTION,
            )
            if final_result.text:
                return final_result.text
        except Exception as exc:
            logger.warning(
                "Final order summary generation failed",
                extra={"event": "order_support_summary_fallback", "error": type(exc).__name__},
            )

        return "I have checked your order records, but could not produce a summary. Please verify your order number and try again."

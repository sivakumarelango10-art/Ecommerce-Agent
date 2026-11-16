import os
import pandas as pd
from mcp.server.fastmcp import FastMCP

excel_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.xlsx")

mcp = FastMCP("ecommerce-orders")


@mcp.tool()
def get_order_by_id(order_id: str) -> str:
    """Get full details of one order using its order id, for example ORD1001."""
    df = pd.read_excel(excel_file)
    result = df[df["order_id"].str.upper() == order_id.strip().upper()]
    if result.empty:
        return "No order found with id " + order_id
    return result.to_string(index=False)


@mcp.tool()
def get_orders_by_customer(name_or_email: str) -> str:
    """Get all orders of a customer using their name or email."""
    df = pd.read_excel(excel_file)
    text = name_or_email.strip().lower()
    result = df[df["customer_name"].str.lower().str.contains(text) | df["email"].str.lower().str.contains(text)]
    if result.empty:
        return "No orders found for " + name_or_email
    return result.to_string(index=False)


@mcp.tool()
def get_all_orders() -> str:
    """Get the list of all orders. Use this for general questions like counts or status summaries."""
    df = pd.read_excel(excel_file)
    return df.to_string(index=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")

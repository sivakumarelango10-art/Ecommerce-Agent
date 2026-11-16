import os
import sys
from dotenv import load_dotenv
from crewai import Agent, Task, Crew
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters

load_dotenv()

server_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
server_params = StdioServerParameters(
    command=sys.executable,
    args=[server_file],
    env=dict(os.environ),
)

mcp_adapter = MCPServerAdapter(server_params)
order_tools = mcp_adapter.tools
print("MCP tools loaded:", [tool.name for tool in order_tools])

order_agent = Agent(
    role="E-commerce Order Support Agent",
    goal="Answer customer questions about their orders using the order database",
    backstory=(
        "You are a friendly customer support agent for an online store. "
        "You always use your tools to look up real order data. "
        "Never make up order details. If an order is not found, say so politely."
    ),
    tools=order_tools,
    llm="gpt-4o-mini",
    verbose=True,
)

order_task = Task(
    description="Customer question: {question}",
    expected_output="A short, clear and friendly answer with the order details the customer asked for.",
    agent=order_agent,
)

crew = Crew(
    agents=[order_agent],
    tasks=[order_task],
    verbose=True,
)

if __name__ == "__main__":
    answer = crew.kickoff(inputs={"question": "What is the status of order ORD1002?"})
    print(answer)
    mcp_adapter.stop()

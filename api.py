from fastapi import FastAPI
from pydantic import BaseModel
from agent import crew

app = FastAPI(title="E-commerce Order Agent API")


class Question(BaseModel):
    question: str


@app.get("/")
def home():
    return {"message": "Order agent API is running. POST your question to /ask"}


@app.post("/ask")
def ask(body: Question):
    result = crew.kickoff(inputs={"question": body.question})
    return {"question": body.question, "answer": result.raw}

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.constants import MAX_QUESTION_LENGTH


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_LENGTH)]


class AnswerResponse(BaseModel):
    answer: str

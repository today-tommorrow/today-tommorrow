from typing import Annotated

from pydantic import Field

from app.ai.questions import ContractModel


class SubmitRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
    review_token: Annotated[
        str, Field(min_length=67, max_length=67, pattern=r"^v1\.[0-9a-f]{64}$")
    ]

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.routes import AuthError, auth_error_handler
from app.auth.routes import router as auth_router
from app.db import get_session

app = FastAPI(title="Today Tommorrow API")
app.add_exception_handler(AuthError, auth_error_handler)
app.include_router(auth_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/api/health", response_model=HealthResponse)
def health(session: Annotated[Session, Depends(get_session)]) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="database_unavailable") from None
    return HealthResponse(status="ok")

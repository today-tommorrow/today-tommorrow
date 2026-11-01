from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.routes import AuthError, auth_error_handler
from app.auth.routes import router as auth_router
from app.db import get_session
from app.intake.question_routes import question_error_handler
from app.intake.question_routes import router as question_router
from app.intake.question_service import QuestionServiceError
from app.intake.routes import ProfileError, profile_error_handler
from app.intake.routes import router as intake_router
from app.jobs.routes import JobHTTPError, job_error_handler
from app.jobs.routes import router as jobs_router
from app.saved_courses.routes import SavedCourseError, error_handler
from app.saved_courses.routes import router as saved_router

app = FastAPI(title="Today Tommorrow API")
app.add_exception_handler(AuthError, auth_error_handler)
app.include_router(auth_router)
app.add_exception_handler(ProfileError, profile_error_handler)
app.include_router(intake_router)
app.add_exception_handler(QuestionServiceError, question_error_handler)
app.include_router(question_router)
app.add_exception_handler(JobHTTPError, job_error_handler)
app.include_router(jobs_router)
app.add_exception_handler(SavedCourseError, error_handler)
app.include_router(saved_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/api/health", response_model=HealthResponse)
def health(session: Annotated[Session, Depends(get_session)]) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="database_unavailable") from None
    return HealthResponse(status="ok")

"""Serialize application errors for transport boundaries."""

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from schedule_core import calendar_service as service
from schedule_core.contracts import CommandError, FailureResult, FieldIssue


def failure(error):
    code = "validation"
    issues = []
    conflicts = []
    if isinstance(error, service.ApplicationError):
        code = error.code
        conflicts = error.conflicts
    elif isinstance(error, SQLAlchemyError):
        code = "storage"
    elif isinstance(error, ValidationError):
        issues = [
            FieldIssue(path=".".join(map(str, e["loc"])), message=e["msg"])
            for e in error.errors()
        ]
    message = (
        "Database operation failed"
        if code == "storage"
        else (
            "Invalid input data: " + str(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
    )
    result = FailureResult(
        error=message,
        problem=CommandError(code=code, message=message, issues=issues),
        conflicts=conflicts,
    ).model_dump(mode="json")
    if conflicts:
        result["details"] = {
            "conflicts": [
                {"type": "instance", "event": c.model_dump(mode="json")}
                for c in conflicts
            ],
            "alternatives": [],
        }
    return result


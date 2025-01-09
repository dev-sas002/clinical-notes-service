"""Exception handlers.

Clients get a stable, non-leaky error envelope; operators get the detail in the
logs. A 500 body never contains an exception message, because on this service
an exception message can contain a patient's data.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.tenancy import MissingTenantContextError
from app.services.care_notes import CareNoteNotFoundError

logger = logging.getLogger(__name__)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the application's exception handlers."""

    @app.exception_handler(CareNoteNotFoundError)
    async def _not_found(request: Request, exc: CareNoteNotFoundError) -> JSONResponse:
        # Deliberately identical whether the note does not exist at all or
        # belongs to a different tenant: a 403 here would confirm that some
        # other tenant holds that id.
        return _error(status.HTTP_404_NOT_FOUND, "Care note not found.")

    @app.exception_handler(MissingTenantContextError)
    async def _missing_tenant(request: Request, exc: MissingTenantContextError) -> JSONResponse:
        # Reaching the database with no tenant bound is a bug in this service,
        # not a bad request -- surface it as a 500 and log it loudly.
        logger.error("Tenant scope missing for %s %s", request.method, request.url.path)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error.")

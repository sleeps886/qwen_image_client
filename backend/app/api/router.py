"""Router registration only; keep business logic out of the composition root."""
from fastapi import APIRouter
from backend.app.api.routers import system,logs,recognition,results,evaluation,inversion

api_router=APIRouter()
for module in (system,logs,recognition,results,evaluation,inversion):
    api_router.include_router(module.router)

from fastapi import APIRouter

from app.api.v1 import tasks, upload
from app.routes.presets import router as presets_router

router = APIRouter()

router.include_router(upload.router)
router.include_router(tasks.router)
router.include_router(presets_router)

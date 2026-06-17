from fastapi import APIRouter, Depends

from app.api.deps.auth import require_camera_auth, require_internal_auth
from app.api.routes import access, aibox_mock, auth, face, health, history, ocr, tickets

api_router = APIRouter()

# Public routes
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])

# Internal APIs: dành cho FE/user nội bộ
api_router.include_router(ocr.router, tags=["ocr"], dependencies=[Depends(require_internal_auth)])
api_router.include_router(face.router, tags=["face"], dependencies=[Depends(require_internal_auth)])
api_router.include_router(access.router, tags=["access"], dependencies=[Depends(require_internal_auth)])
api_router.include_router(tickets.router, tags=["tickets"], dependencies=[Depends(require_internal_auth)])
api_router.include_router(history.router, tags=["history"], dependencies=[Depends(require_internal_auth)])

# Camera APIs: dành cho Camera/AIBox/service
api_router.include_router(aibox_mock.router, tags=["mock-aibox"], dependencies=[Depends(require_camera_auth)])
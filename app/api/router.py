from fastapi import APIRouter

from app.api.routes import access, aibox_mock, face, health, history, ocr, tickets

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(ocr.router, tags=["ocr"])
api_router.include_router(aibox_mock.router, tags=["mock-aibox"])
api_router.include_router(face.router, tags=["face"])
api_router.include_router(access.router, tags=["access"])
api_router.include_router(tickets.router, tags=["tickets"])
api_router.include_router(history.router, tags=["history"])

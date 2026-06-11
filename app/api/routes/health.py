from __future__ import annotations
import asyncio
import os
import sqlite3
import uuid
from typing import Optional, Dict, Any, List, Tuple
from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException, Query, Depends
from app.database import get_db, DB_PATH
from app.core.config import settings
from app.services.access_service import *
from app.services.ocr_service import extract_cccd
from app.services.face_service import compare_face_image_paths, get_face_model_status

router = APIRouter()

@router.get("/")
def root():
    return {
        "message": "OCR API running with SQLite database",
        "docs": "/docs",
        "database": DB_PATH,
        "time_format": "Asia/Ho_Chi_Minh - YYYY-MM-DD HH:mm:ss",
        "face_model": get_face_model_status(),
        "main_flow": [
            "POST /mock/aibox/lpr-event để giả lập camera phát hiện xe và lưu DB",
            "POST /ocr/cccd với event_uid để ghép CCCD vào xe và lưu DB",
            "POST /api/v1/face/compare để so sánh mặt thật bằng InsightFace",
            "POST /mock/face/compare để giả lập kết quả so sánh mặt và cập nhật DB",
            "GET /api/v1/access/history để FE lấy lịch sử từ DB",
            "PERSON_ONLY sau face compare sẽ tự khóa link_policy=PERSON_ONLY_LOCKED",
        ],
    }


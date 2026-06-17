"""Auth context objects injected into request.state after auth guards pass.

Tách context để service/route không phải đọc trực tiếp JWT/header ở nhiều nơi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class InternalAuthContext:
    """Context của người dùng nội bộ sau khi verify internal JWT/session."""

    user_id: str
    email: str
    organization_id: str
    roles: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    token_type: str = "internal"

    def has_permission(self, permission_code: str) -> bool:
        return "*" in self.permissions or permission_code in self.permissions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "organization_id": self.organization_id,
            "roles": self.roles,
            "permissions": self.permissions,
            "token_type": self.token_type,
        }


@dataclass(frozen=True)
class CameraAuthContext:
    """Context của camera/service sau khi verify Bearer token + X-Organization-ID."""

    camera_client_id: str
    camera_code: str
    camera_name: Optional[str]
    organization_id: str
    scope: List[str] = field(default_factory=list)
    token_id: Optional[int] = None
    token_type: str = "camera"

    def has_scope(self, scope_code: str) -> bool:
        return "*" in self.scope or scope_code in self.scope

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_client_id": self.camera_client_id,
            "camera_code": self.camera_code,
            "camera_name": self.camera_name,
            "organization_id": self.organization_id,
            "scope": self.scope,
            "token_id": self.token_id,
            "token_type": self.token_type,
        }

_ROLE_SEED = [
    ("ADMIN", "Admin", "Quản lý hệ thống", 1),
    ("MANAGER", "Manager", "Quản lý cơ sở", 1),
    ("GUARD", "Guard", "Bảo vệ trực công", 1),
]

_PERMISSION_SEED = [
    ("camera.view", "Xem camera", "camera"),
    ("camera.manage", "Quản lý camera", "camera"),
    ("ticket.issue", "Phát hành vé", "ticket"),
    ("card.link", "Gắn thẻ vào phiên xe", "card"),
    ("card.checkout", "Checkout bằng thẻ", "card"),
    ("card.manage", "Quản lý kho thẻ (đăng ký/khóa thẻ)", "card"),
    ("ticket.print", "In vé", "ticket"),
    ("vehicle.approve", "Duyệt xe ra/vào", "vehicle"),
    ("report.export", "Xuất báo cáo", "report"),
    ("system.user.create", "Tạo user", "system"),
    ("user.read", "Xem user", "system"),
    ("system.user.update", "Sửa user", "system"),
    ("system.user.delete", "Xóa user", "system"),
    ("role.assign", "Gán role", "system"),
    ("permission.assign", "Gán permission", "system"),
    ("system.org.create", "Tạo tổ chức", "system"),
    ("system.org.update", "Sửa tổ chức", "system"),
    ("system.org.delete", "Khóa tổ chức", "system"),
    ("role.create", "Tạo role", "system"),
    ("role.read", "Xem role", "system"),
    ("role.update", "Sửa role", "system"),
    ("role.delete", "Xóa role", "system"),
    ("permission.read", "Xem permission", "system"),
]

_ROLE_PERMISSION_SEED = {
    "ADMIN": ["*"],
    "MANAGER": [
        "camera.view", "camera.manage", "ticket.issue", "ticket.print",
        "vehicle.approve", "report.export",
        "system.user.create", "system.user.update", "system.user.delete",
        "user.read",
        "role.create", "role.read", "role.update", "role.delete", "role.assign",
        "permission.read", "permission.assign",
        "system.org.create", "system.org.update", "system.org.delete",
        "card.link", "card.checkout"
    ],
    "GUARD": ["camera.view", "ticket.issue", "ticket.print", "vehicle.approve", "card.link", "card.checkout"],
}

_LEGACY_ROLE_MAP = {
    "admin": "ADMIN",
    "guard": "GUARD",
    "manager": "MANAGER",
}

import hashlib
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import Request
from PIL import Image, ImageDraw, ImageFont
import qrcode
from qrcode.constants import ERROR_CORRECT_M

try:
    import barcode
    from barcode.writer import ImageWriter
except Exception:  # pragma: no cover - fallback khi môi trường chưa cài python-barcode
    barcode = None
    ImageWriter = None

from app.core.files import absolute_url, static_url_to_local_path, to_static_url

TICKETS_FOLDER = "static/tickets"


def mask_cccd(value: Optional[str]) -> Optional[str]:
    """Ẩn bớt CCCD khi in vé, tránh đưa toàn bộ thông tin nhạy cảm lên vé."""
    if not value:
        return None
    value = str(value)
    if len(value) <= 6:
        return value
    return value[:3] + "*" * max(0, len(value) - 6) + value[-3:]


def load_ticket_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for font_path in candidates:
        if font_path and os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def draw_centered(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], width: int, text: str, font: ImageFont.ImageFont, fill=(0, 0, 0)) -> None:
    x, y = xy
    draw.text((x + (width - text_width(draw, text, font)) // 2, y), text, font=font, fill=fill)


def generate_qr_image(qr_value: str, out_path: str) -> None:
    """Tạo QR Code thật, scan được bằng camera điện thoại."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(qr_value)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_img.save(out_path)


def generate_barcode_image(ticket_code: str, out_path: str) -> None:
    """Tạo barcode Code128 thật nếu có python-barcode."""
    if barcode is not None and ImageWriter is not None:
        code128 = barcode.get("code128", ticket_code, writer=ImageWriter())
        base_path, _ = os.path.splitext(out_path)
        generated_path = code128.save(
            base_path,
            options={
                "module_width": 0.35,
                "module_height": 22,
                "quiet_zone": 3,
                "write_text": False,
            },
        )
        if generated_path != out_path and os.path.exists(generated_path):
            os.replace(generated_path, out_path)
        return

    # Fallback để demo vẫn chạy nếu máy chưa cài python-barcode.
    width, height = 420, 90
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    digest = hashlib.sha256(ticket_code.encode("utf-8")).digest()
    bits = "".join(f"{byte:08b}" for byte in digest)
    x = 12
    for i, bit in enumerate(bits[:180]):
        bar_w = 1 if i % 3 else 2
        if bit == "1":
            draw.rectangle([x, 8, x + bar_w, height - 24], fill="black")
        x += bar_w + 1
        if x >= width - 12:
            break
    img.save(out_path)


def build_ticket_qr_value(request: Request, ticket_code: str) -> str:
    """QR chứa URL verify vé để FE/mobile scan có thể tra vé.

    URL này là GET nên quét QR bằng trình duyệt vẫn xem được thông tin vé.
    Checkout thật vẫn nên qua POST /api/v1/tickets/checkout.
    """
    return str(request.base_url).rstrip("/") + f"/api/v1/tickets/verify/{ticket_code}"


def render_ticket_images(
    request: Request,
    ticket_code: str,
    ticket_id: str,
    session: Dict[str, Any],
    person_log: Optional[Dict[str, Any]],
    vehicle_log: Optional[Dict[str, Any]],
    ticket_type: str,
) -> Dict[str, str]:
    os.makedirs(TICKETS_FOLDER, exist_ok=True)

    front_path = os.path.join(TICKETS_FOLDER, f"{ticket_code}-front.png")
    back_path = os.path.join(TICKETS_FOLDER, f"{ticket_code}-back.png")
    barcode_path = os.path.join(TICKETS_FOLDER, f"{ticket_code}-barcode.png")
    qr_path = os.path.join(TICKETS_FOLDER, f"{ticket_code}-qr.png")

    qr_value = build_ticket_qr_value(request, ticket_code)
    generate_barcode_image(ticket_code, barcode_path)
    generate_qr_image(qr_value, qr_path)

    title_font = load_ticket_font(24, bold=True)
    label_font = load_ticket_font(17, bold=True)
    text_font = load_ticket_font(17)
    small_font = load_ticket_font(13)

    card_w, card_h = 640, 360
    border = (40, 105, 190)
    header = "Petrol Atromex"
    full_name = (person_log or {}).get("full_name") or session.get("full_name") or "-"
    cccd_mask = mask_cccd((person_log or {}).get("cccd_number") or session.get("cccd_number")) or "-"
    plate = (vehicle_log or {}).get("plate_number") or session.get("expected_plate_number") or "-"
    checked_in_at = session.get("checked_in_at") or "-"
    checked_out_at = session.get("checked_out_at") or "-"

    # Mặt trước
    front = Image.new("RGB", (card_w, card_h), "white")
    draw = ImageDraw.Draw(front)
    draw.rounded_rectangle([10, 10, card_w - 10, card_h - 10], radius=28, outline=border, width=3)
    draw_centered(draw, (0, 30), card_w, header, title_font)
    draw.text((50, 95), "Tên:", font=label_font, fill="black")
    draw.text((140, 95), full_name, font=text_font, fill="black")
    draw.text((50, 135), "ID:", font=label_font, fill="black")
    draw.text((140, 135), cccd_mask, font=text_font, fill="black")
    draw.text((50, 175), "Biển số:", font=label_font, fill="black")
    draw.text((140, 175), plate, font=text_font, fill="black")
    draw.text((50, 215), "Ngày vào:", font=label_font, fill="black")
    draw.text((170, 215), checked_in_at, font=text_font, fill="black")
    draw.text((50, 255), "Ngày ra:", font=label_font, fill="black")
    draw.text((170, 255), checked_out_at, font=text_font, fill="black")
    draw.text((50, 305), f"Mã vé: {ticket_code}", font=small_font, fill=(70, 70, 70))

    face_local_path = static_url_to_local_path((person_log or {}).get("cccd_face_image_url") or (person_log or {}).get("live_face_image_url"))
    face_box = [440, 105, 575, 240]
    draw.rounded_rectangle(face_box, radius=14, outline=(90, 90, 90), width=3)
    if face_local_path and os.path.exists(face_local_path):
        try:
            face = Image.open(face_local_path).convert("RGB")
            face.thumbnail((face_box[2] - face_box[0] - 10, face_box[3] - face_box[1] - 10))
            fx = face_box[0] + ((face_box[2] - face_box[0]) - face.width) // 2
            fy = face_box[1] + ((face_box[3] - face_box[1]) - face.height) // 2
            front.paste(face, (fx, fy))
        except Exception:
            draw.text((455, 160), "FACE", font=label_font, fill=(90, 90, 90))
    else:
        draw.text((455, 160), "FACE", font=label_font, fill=(90, 90, 90))
    front.save(front_path)

    # Mặt sau
    back = Image.new("RGB", (card_w, card_h), "white")
    draw = ImageDraw.Draw(back)
    draw.rounded_rectangle([10, 10, card_w - 10, card_h - 10], radius=28, outline=border, width=3)
    draw_centered(draw, (0, 30), card_w, header, title_font)
    draw.text((50, 90), "Thông tin thêm", font=label_font, fill="black")
    draw.text((50, 125), f"Loại vé: {ticket_type}", font=text_font, fill="black")
    draw.text((50, 160), f"Phiên: {session.get('session_code') or session.get('session_id')}", font=text_font, fill="black")
    draw.text((50, 195), f"Cổng: {session.get('gate_name') or '-'}", font=text_font, fill="black")

    barcode_img = Image.open(barcode_path).convert("RGB")
    barcode_img.thumbnail((380, 85))
    back.paste(barcode_img, (55, 245))

    qr_img = Image.open(qr_path).convert("RGB")
    qr_size = 150
    qr_img.thumbnail((qr_size, qr_size))
    right_section_x = 420
    right_section_w = card_w - right_section_x - 20
    qr_x = right_section_x + (right_section_w - qr_img.width) // 2
    qr_y = (card_h - qr_img.height) // 2
    back.paste(qr_img, (qr_x, qr_y))

    back.save(back_path)

    return {
        "front_image_url": absolute_url(request, to_static_url(front_path)),
        "back_image_url": absolute_url(request, to_static_url(back_path)),
        "barcode_image_url": absolute_url(request, to_static_url(barcode_path)),
        "qr_image_url": absolute_url(request, to_static_url(qr_path)),
        "qr_value": qr_value,
    }

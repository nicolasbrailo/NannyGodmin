import io
import socket
import uuid
from base64 import b64encode

import qrcode

import db


class ValidationError(Exception):
    pass


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def make_qr_code(host):
    ip = get_local_ip()
    port = host.split(":")[-1] if ":" in host else "80"
    base_url = f"http://{ip}:{port}/"
    qr_data = f"nannygodmin://config?url={base_url}"
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = b64encode(buf.getvalue()).decode("utf-8")
    return qr_b64, qr_data


def provision(conn, device_name, android_id, config):
    if not android_id:
        raise ValidationError("androidId is required")

    existing = db.find_device_by_android_id(conn, android_id)
    if existing:
        return {"clientId": existing["id"], "locked": bool(existing["locked"]), **config}

    client_id = str(uuid.uuid4())
    db.insert_device(conn, client_id, device_name, android_id)
    return {"clientId": client_id, "locked": False, **config}

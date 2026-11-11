from __future__ import annotations

import io

import segno


def qr_png(data: str, scale: int = 8) -> bytes:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=scale, border=3)
    return buf.getvalue()

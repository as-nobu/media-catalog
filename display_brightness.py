"""Brightness correction for display copies of cached thumbnails only.

Use one intensity-dependent RGB multiplier to preserve channel ratios. The
90th percentile of visible, non-black pixels determines a bounded gamma lift;
small bright labels/highlights need not prevent dark fluorescence from being
visible. Strength 0 is an exact bypass; 50 matches the previous mild correction.
Strength 100 doubles that lift (gain is capped at 5). This is a viewing aid, not a
quantitative intensity correction. No files, database writes or model access.
"""
import math

import numpy as np
from PySide6.QtGui import QImage


def clamp_strength(value):
    try:
        return max(0, min(100, int(value)))
    except (ValueError, TypeError, OverflowError):
        return 0


def saved_strength(db):
    value = db.get_setting('display_brightness_strength', None)
    if value is None:
        return 50 if db.get_setting('display_auto_brightness', '0') == '1' else 0
    return clamp_strength(value)


def auto_brightness(image, strength=50):
    strength = clamp_strength(strength)
    if not strength or image.isNull():
        return image
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(
        rgba.height(), rgba.bytesPerLine())[:, :rgba.width() * 4].reshape(
            rgba.height(), rgba.width(), 4)
    rgb = pixels[:, :, :3]
    intensity = rgb.max(axis=2)
    visible = (pixels[:, :, 3] > 0) & (intensity > 0)
    values = intensity[visible]
    if not values.size:
        return image
    histogram = np.bincount(values, minlength=256)
    reference = int(np.searchsorted(histogram.cumsum(), math.ceil(values.size * .90)))
    target = 160
    if reference >= target:
        return image
    gamma = max(.65, min(1.0, math.log(target / 255) / math.log(reference / 255)))
    levels = np.arange(1, 256, dtype=np.float32)
    gain = np.ones(256, dtype=np.float32)
    gain[1:] = np.minimum(3.0, (levels / 255) ** gamma * 255 / levels)
    gain = 1 + (gain - 1) * (strength / 50)
    # Limit the common multiplier before applying it, preserving RGB ratios
    # even when the user increases the lift above the previous mild setting.
    gain[1:] = np.minimum(gain[1:], 255 / levels)
    result = pixels.copy()
    result[:, :, :3] = np.clip(np.rint(rgb * gain[intensity, None]), 0, 255).astype(np.uint8)
    # Transparent pixels have no display meaning; preserve their hidden RGB too.
    result[~visible] = pixels[~visible]
    output = QImage(result.data, rgba.width(), rgba.height(), result.strides[0],
                    QImage.Format.Format_RGBA8888).copy()
    output.setDevicePixelRatio(image.devicePixelRatio())
    output.setColorSpace(image.colorSpace())
    output.setDotsPerMeterX(image.dotsPerMeterX())
    output.setDotsPerMeterY(image.dotsPerMeterY())
    return output

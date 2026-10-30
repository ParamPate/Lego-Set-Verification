"""Adjustable contour detection and lossless cropping from the source image."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Settings:
    saturation: int = 108
    brightness_contrast: int = 24
    very_light: int = 218
    wood_brightness: int = 9
    wood_darkness: int = 13
    background_blur: int = 52
    open_size: int = 3
    close_size: int = 7
    close_iterations: int = 1
    minimum_area: int = 500
    padding: int = 35
    reject_border_artifacts: bool = True
    background_mode: str = "Auto"
    white_saturation: int = 70
    white_contrast: int = 24


@dataclass(frozen=True)
class Detection:
    contour: np.ndarray
    x: int
    y: int
    width: int
    height: int
    area: float


def next_run_folders(parent: Path) -> tuple[Path, Path]:
    """Return paired images/analysis folders without overwriting prior runs."""
    number = 0
    while True:
        suffix = "" if number == 0 else f"{number:02d}"
        images = parent / f"images{suffix}"
        analysis = parent / f"analysis{suffix}"
        if not images.exists() and not analysis.exists():
            return images, analysis
        number += 1


def next_images_folder(parent: Path) -> Path:
    """Backward-compatible helper returning the next crop folder."""
    return next_run_folders(parent)[0]


def crop_filename(folder: Path, number: int) -> str:
    """Return a filename unique to this numbered segmentation run."""
    prefix = folder.name.replace("images", "image", 1)
    return f"{prefix}_{number:03d}.png"


def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 else value + 1


def build_mask(image: np.ndarray, settings: Settings) -> np.ndarray:
    """Create a foreground mask for dark or white backgrounds."""
    height, width = image.shape[:2]
    scale = max(height, width) / 1600.0
    working_size = (max(1, round(width / scale)), max(1, round(height / scale)))
    working = cv2.resize(image, working_size, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)

    sigma = max(1, settings.background_blur)
    local_gray = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    brighter = gray.astype(np.int16) - local_gray.astype(np.int16)

    mode = str(settings.background_mode).strip().casefold()
    if mode == "auto":
        # Broad white paper is bright and weakly saturated even when lighting
        # is uneven. Dark tables fail at least one of these conditions.
        mode = (
            "white"
            if float(np.median(gray)) >= 145 and float(np.median(hsv[:, :, 1])) <= 75
            else "dark"
        )

    if mode == "white":
        # Colored pieces differ in saturation; dark and white pieces are found
        # from their shadows/edges relative to a smooth local paper estimate.
        white_sigma = max(10, min(int(settings.background_blur), 40))
        paper = cv2.GaussianBlur(gray, (0, 0), sigmaX=white_sigma, sigmaY=white_sigma)
        darker_than_paper = paper.astype(np.int16) - gray.astype(np.int16)
        mask = (
            (
                (hsv[:, :, 1] >= settings.white_saturation)
                | (darker_than_paper >= settings.white_contrast)
            ).astype(np.uint8)
        ) * 255
    else:
        strongly_saturated = hsv[:, :, 1] >= settings.saturation
        wood_hue = (hsv[:, :, 0] < 25) | (hsv[:, :, 0] > 172)
        saturated = strongly_saturated & (
            (~wood_hue)
            | (brighter >= settings.wood_brightness)
            | (brighter <= -settings.wood_darkness)
        )
        light = brighter >= settings.brightness_contrast
        very_light = (hsv[:, :, 2] >= settings.very_light) & (brighter >= 10)
        mask = ((saturated | light | very_light).astype(np.uint8)) * 255

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones((_odd(settings.open_size), _odd(settings.open_size)), np.uint8),
    )
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((_odd(settings.close_size), _odd(settings.close_size)), np.uint8),
        iterations=max(1, settings.close_iterations),
    )
    return cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)


def find_detections(mask: np.ndarray, settings: Settings) -> list[Detection]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections: list[Detection] = []
    image_area = mask.shape[0] * mask.shape[1]
    scale = max(mask.shape[:2]) / 4096.0
    tiny_dimension = max(12, round(45 * scale))
    small_border_area = 2000 * scale * scale
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, width, height = cv2.boundingRect(contour)
        if area < settings.minimum_area or area > image_area * 0.35:
            continue
        if width < 12 or height < 12:
            continue
        if settings.reject_border_artifacts:
            touches_top = y == 0
            touches_bottom = y + height >= mask.shape[0]
            touches_left = x == 0
            touches_right = x + width >= mask.shape[1]
            if (touches_left or touches_right) and height > mask.shape[0] * 0.5:
                continue
            if (touches_top or touches_bottom) and width > mask.shape[1] * 0.5:
                continue
            if touches_top and width > height * 2.2:
                continue
            if touches_right and area < small_border_area:
                continue
            if width < tiny_dimension and height < tiny_dimension:
                continue
        detections.append(Detection(contour, x, y, width, height, area))
    detections.sort(key=lambda item: (item.y + item.height // 2, item.x + item.width // 2))
    return detections


def analyze(image_path: Path, settings: Settings) -> tuple[np.ndarray, np.ndarray, list[Detection]]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    mask = build_mask(image, settings)
    return image, mask, find_detections(mask, settings)


def make_overview(
    image: np.ndarray,
    detections: list[Detection],
    padding: int,
    selected: set[int] | None = None,
) -> np.ndarray:
    overview = image.copy()
    height, width = image.shape[:2]
    for number, detection in enumerate(detections, 1):
        x1 = max(0, detection.x - padding)
        y1 = max(0, detection.y - padding)
        x2 = min(width, detection.x + detection.width + padding)
        y2 = min(height, detection.y + detection.height + padding)
        is_selected = selected is not None and number - 1 in selected
        color = (40, 40, 230) if is_selected else (40, 210, 40)
        cv2.rectangle(overview, (x1, y1), (x2 - 1, y2 - 1), color, 7)
        label_y = max(35, y1 + 36)
        cv2.putText(overview, str(number), (x1 + 8, label_y), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (0, 0, 0), 7, cv2.LINE_AA)
        cv2.putText(overview, str(number), (x1 + 8, label_y), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (255, 255, 255), 3, cv2.LINE_AA)
    return overview


def save_original_crops(
    image: np.ndarray,
    mask: np.ndarray,
    detections: list[Detection],
    output_parent: Path,
    padding: int,
) -> tuple[Path, Path]:
    """Save rectangular pixels copied directly from the original image."""
    folder, analysis_folder = next_run_folders(output_parent)
    folder.mkdir(parents=True)
    analysis_folder.mkdir(parents=True)
    height, width = image.shape[:2]
    rows: list[dict[str, int | float | str]] = []
    for number, detection in enumerate(detections, 1):
        x1 = max(0, detection.x - padding)
        y1 = max(0, detection.y - padding)
        x2 = min(width, detection.x + detection.width + padding)
        y2 = min(height, detection.y + detection.height + padding)
        crop = image[y1:y2, x1:x2]  # unmodified original BGR pixels
        filename = crop_filename(folder, number)
        cv2.imwrite(str(folder / filename), crop, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        rows.append({
            "number": number, "filename": filename, "x": x1, "y": y1,
            "width": x2 - x1, "height": y2 - y1,
            "contour_area": round(detection.area, 1),
        })
    cv2.imwrite(str(analysis_folder / "numbered_overview.jpg"), make_overview(image, detections, padding), [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(analysis_folder / "detection_mask.png"), mask)
    with (analysis_folder / "detections.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ("number", "filename", "x", "y", "width", "height", "contour_area")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return folder, analysis_folder

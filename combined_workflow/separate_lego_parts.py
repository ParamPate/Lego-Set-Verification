"""Extract separated LEGO objects photographed on a pale, plain background.

Unlike the original fixed grayscale threshold, this detector combines colour
saturation, local background difference, and closed edges.  That makes it much
less sensitive to uneven lighting and keeps pale/white parts visible.

Usage:
    python separate_lego_parts.py photo.jpg
    python separate_lego_parts.py photo.jpg --output my_parts --min-area 1200
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    contour: np.ndarray
    x: int
    y: int
    width: int
    height: int
    area: float


def build_foreground_mask(image: np.ndarray) -> np.ndarray:
    """Return a binary foreground mask for objects on a pale background."""
    height, width = image.shape[:2]
    scale = max(height, width) / 1600.0
    small_size = (max(1, round(width / scale)), max(1, round(height / scale)))
    working = cv2.resize(image, small_size, interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)

    if float(np.median(hsv[:, :, 2])) < 150:
        return build_dark_background_mask(working, (width, height))

    # Coloured plastic is the strongest and cleanest cue. The threshold is
    # deliberately above the warm cast present in cream/white backdrops.
    colourful = (hsv[:, :, 1] >= 72).astype(np.uint8) * 255

    # Neutral grey/black parts have little saturation, so retain sufficiently
    # dark pixels separately. Keeping this absolute and conservative avoids
    # turning broad, soft shadows into object bridges.
    darker = (gray <= 132).astype(np.uint8) * 255

    # Close object outlines so white pieces become solid regions.
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    enclosed = np.zeros_like(edges)
    maximum_outline_area = working.shape[0] * working.shape[1] * 0.025
    usable_outlines = [
        contour
        for contour in contours
        if 45 <= cv2.contourArea(contour) <= maximum_outline_area
    ]
    cv2.drawContours(enclosed, usable_outlines, -1, 255, thickness=cv2.FILLED)

    mask = cv2.bitwise_or(colourful, darker)
    # Edge-filled regions help pale parts, but only keep edge pixels close to
    # an independently supported colour/dark region. This prevents long
    # background creases and shadows from joining distant pieces.
    supported = cv2.dilate(mask, np.ones((31, 31), np.uint8), iterations=1)
    mask = cv2.bitwise_or(mask, cv2.bitwise_and(enclosed, supported))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

    # Suppress tiny texture/noise before returning to original resolution.
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    minimum_working_area = max(70, int(mask.size * 0.000025))
    for label in range(1, component_count):
        if stats[label, cv2.CC_STAT_AREA] >= minimum_working_area:
            cleaned[labels == label] = 255

    return cv2.resize(cleaned, (width, height), interpolation=cv2.INTER_NEAREST)


def build_dark_background_mask(
    working: np.ndarray, original_size: tuple[int, int]
) -> np.ndarray:
    """Build a contour mask for parts on a darker wood/cloth surface."""
    hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)

    # Heavy smoothing removes the fine wood grain while retaining the broad
    # illumination gradient across the tabletop.
    local_gray = cv2.GaussianBlur(gray, (0, 0), sigmaX=52, sigmaY=52)
    brighter = gray.astype(np.int16) - local_gray.astype(np.int16)

    # Strong chroma identifies green/blue/orange/brown pieces. Local brightness
    # retains white, grey, and pale pieces. Avoid a general colour-distance
    # mask: on wood it follows grain bands and bridges distant parts.
    strongly_saturated = hsv[:, :, 1] >= 108
    # Orange/brown wood can itself be saturated. For that hue range, also
    # require local luminance contrast; vivid greens/blues/purples remain
    # strong foreground cues on their own.
    wood_hue = (hsv[:, :, 0] < 25) | (hsv[:, :, 0] > 172)
    saturated = strongly_saturated & (
        (~wood_hue) | (brighter >= 9) | (brighter <= -13)
    )
    light = brighter >= 24
    very_light = (hsv[:, :, 2] >= 218) & (brighter >= 10)
    mask = ((saturated | light | very_light).astype(np.uint8)) * 255

    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1
    )
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=1
    )

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    minimum_area = max(65, int(mask.size * 0.000022))
    maximum_area = int(mask.size * 0.12)
    for label in range(1, count):
        area = stats[label, cv2.CC_STAT_AREA]
        if minimum_area <= area <= maximum_area:
            cleaned[labels == label] = 255
    return cv2.resize(cleaned, original_size, interpolation=cv2.INTER_NEAREST)


def find_detections(mask: np.ndarray, minimum_area: int) -> list[Detection]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections: list[Detection] = []
    image_area = mask.shape[0] * mask.shape[1]
    scale = max(mask.shape[:2]) / 4096.0
    tiny_dimension = max(12, round(45 * scale))
    small_border_area = 2000 * scale * scale
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, width, height = cv2.boundingRect(contour)
        if area < minimum_area or area > image_area * 0.35:
            continue
        if width < 12 or height < 12:
            continue
        touches_top = y == 0
        touches_right = x + width >= mask.shape[1]
        # Grain/reflection fragments in this type of photograph commonly enter
        # through the frame edge. Keep substantial cropped objects, but reject
        # shallow top streaks and small right-edge slivers.
        if touches_top and width > height * 2.2:
            continue
        if touches_right and area < small_border_area:
            continue
        if width < tiny_dimension and height < tiny_dimension:
            continue
        detections.append(Detection(contour, x, y, width, height, area))
    # Human-friendly reading order.
    detections.sort(key=lambda item: (item.y + item.height // 2, item.x + item.width // 2))
    return detections


def save_results(
    image: np.ndarray,
    mask: np.ndarray,
    detections: list[Detection],
    output_folder: Path,
    padding: int,
) -> None:
    output_folder.mkdir(parents=True, exist_ok=True)
    parts_folder = output_folder / "parts"
    parts_folder.mkdir(exist_ok=True)
    height, width = image.shape[:2]
    overview = image.copy()
    rows: list[dict[str, int | float | str]] = []

    for number, detection in enumerate(detections, 1):
        x1 = max(0, detection.x - padding)
        y1 = max(0, detection.y - padding)
        x2 = min(width, detection.x + detection.width + padding)
        y2 = min(height, detection.y + detection.height + padding)
        crop = image[y1:y2, x1:x2].copy()
        crop_mask = mask[y1:y2, x1:x2]

        # Give recognition services a clean white background while retaining
        # the original RGB pixels of the detected object.
        isolated = np.full_like(crop, 255)
        isolated[crop_mask > 0] = crop[crop_mask > 0]
        filename = f"part_{number:03d}.jpg"
        cv2.imwrite(str(parts_folder / filename), isolated, [cv2.IMWRITE_JPEG_QUALITY, 96])

        cv2.rectangle(overview, (x1, y1), (x2 - 1, y2 - 1), (40, 210, 40), 7)
        label_y = max(35, y1 + 36)
        cv2.putText(
            overview,
            str(number),
            (x1 + 8, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.15,
            (0, 0, 0),
            7,
            cv2.LINE_AA,
        )
        cv2.putText(
            overview,
            str(number),
            (x1 + 8, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.15,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
        rows.append(
            {
                "number": number,
                "filename": filename,
                "x": detection.x,
                "y": detection.y,
                "width": detection.width,
                "height": detection.height,
                "contour_area": round(detection.area, 1),
            }
        )

    cv2.imwrite(str(output_folder / "detection_mask.png"), mask)
    cv2.imwrite(str(output_folder / "numbered_overview.jpg"), overview, [cv2.IMWRITE_JPEG_QUALITY, 94])
    with (output_folder / "detections.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys() if rows else ("number", "filename"))
        writer.writeheader()
        writer.writerows(rows)


def analyze_image(
    image_path: Path,
    output_folder: Path | None = None,
    minimum_area: int | None = None,
    padding: int | None = None,
) -> tuple[Path, int]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    image_area = image.shape[0] * image.shape[1]
    if minimum_area is None:
        minimum_area = max(650, round(image_area * 0.000055))
    if padding is None:
        padding = max(18, round(max(image.shape[:2]) * 0.012))
    if output_folder is None:
        output_folder = image_path.with_name(f"{image_path.stem}_separated_parts")

    mask = build_foreground_mask(image)
    detections = find_detections(mask, minimum_area)
    save_results(image, mask, detections, output_folder, padding)
    return output_folder, len(detections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Photograph to analyze")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--min-area", type=int, help="Minimum object area in original-image pixels")
    parser.add_argument("--padding", type=int, help="Padding around each saved object")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output, count = analyze_image(args.image, args.output, args.min_area, args.padding)
    print(f"Saved {count} detected objects to: {output}")


if __name__ == "__main__":
    main()

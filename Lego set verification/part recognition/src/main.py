"""
Identify LEGO parts and colors and persist the backend result files.

The GUI calls ``identify_part()`` for each new photograph, then calls
``append_result_to_csv()`` for a successful result. This module deliberately
contains no GUI code, which keeps recognition, fallback color estimation, and
file output reusable from either the desktop application or a command-line
workflow.

Brickognize supplies the primary part and color predictions. Pillow-based RGB
estimation is retained only as a fallback when the API omits a color.
"""

# ============================================================
# MAINTAINER MAP: RECOGNITION BACKEND
# ============================================================
# gui.py imports identify_part() and append_result_to_csv() from this module.
# Keep this file free of Tkinter widgets so recognition remains reusable.
#
# End-to-end backend flow:
# 1. identify_part() validates and uploads one source image.
# 2. The highest-ranked detected item and BrickLink candidate are selected.
# 3. Part confidence and color confidence are calculated independently.
# 4. Brickognize color is primary; estimate_color() runs only when it is absent.
# 5. The complete response is merged into brickognize_responses.json.
# 6. A normalized result dictionary is returned to gui.py.
# 7. gui.py calls append_result_to_csv() after a successful result.
#
# Critical invariants:
# - PROJECT_ROOT is based on __file__, never the terminal working directory.
# - Part confidence and color confidence must never be combined.
# - BrickLink color IDs are identifiers, not scores.
# - API response JSON preserves raw evidence; CSV provides a working summary.
# - Duplicate filenames are skipped/reused by the GUI to prevent duplicate rows.
# - Network, schema, image, and file errors return safely instead of terminating
#   the entire multi-image batch.
#
# When output fields change, coordinate the same change with review_manager.py,
# excel_report.py, gui.py restore/display logic, and README.md.
# ============================================================

import csv
import json
import math
from pathlib import Path

import requests
from PIL import Image

from bricklink_colors import BRICKLINK_REVIEW_COLORS


# ============================================================
# SK BrickRescue Part Recognition Backend
#
# This program processes individual LEGO images and creates a
# structured files that support the employee review workflow.
#
# For each image, the program:
#
# 1. Sends the image to Brickognize.
# 2. Retrieves the highest-confidence LEGO part prediction.
# 3. Retrieves Brickognize's predicted LEGO color, when available.
# 4. Uses a local Pillow-based color estimate only as a fallback.
# 5. Saves the complete API response as an individual JSON file.
# 6. Appends the result to one shared CSV file.
# 7. Skips images that have already been processed.
#
# The part confidence and color confidence are kept separate
# because they measure two different predictions.
# ============================================================


# ============================================================
# Configuration
# ============================================================

# Brickognize endpoint used by the website to return both part candidates and color candidates.
#
# Important:
# This is an internal Brickognize endpoint and may change without notice. The Pillow color-estimation workflow is retained as a fallback when Brickognize identifies a part but does not return a color candidate.
BRICKOGNIZE_API_URL = (
    "https://api.brickognize.com/internal/search/"
    "?external_catalogs=bricklink&predict_color=true"
)

# Resolve every data folder from this file's project location instead of the
# terminal's current directory. This guarantees that running from VS Code,
# PowerShell, or another shortcut always writes into this project's folders.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Folder containing the individual LEGO images to process.
IMAGES_FOLDER = PROJECT_ROOT / "images"

# Shared CSV file containing one row for every processed image.
RESULTS_FILE = PROJECT_ROOT / "results" / "brickognize_results.csv"

# Shared file containing every complete Brickognize API response.
API_RESPONSES_FILE = (
    PROJECT_ROOT
    / "results"
    / "brickognize_responses.json"
)

# Confidence threshold used to determine whether a part prediction should be reviewed manually.
PART_REVIEW_THRESHOLD = 80.0

# Confidence threshold used to determine whether a color prediction should be reviewed manually.
COLOR_REVIEW_THRESHOLD = 80.0


# ============================================================
# Local LEGO Color Reference Library
# ============================================================

# These RGB values are used only when Brickognize does not return a color prediction. The local method compares the photographed
# color against this small reference library and selects the nearest match.
LEGO_COLORS = {
    "White": (242, 243, 242),
    "Black": (27, 42, 52),
    "Bright Red": (196, 40, 27),
    "Bright Blue": (13, 105, 171),
    "Bright Yellow": (245, 205, 47),
    "Bright Green": (75, 151, 74),
    "Bright Light Orange": (248, 187, 61),
    "Tan": (215, 197, 153),
    "Light Bluish Gray": (160, 165, 169),
    "Dark Bluish Gray": (99, 95, 98),
}


# ============================================================
# Color Helper Functions
# ============================================================

# FUNCTION GUIDE: get_closest_lego_color
# Purpose: Find the closest local LEGO reference color.
def get_closest_lego_color(
    rgb: tuple[int, int, int],
) -> tuple[str, float]:
    """
    Find the closest local LEGO reference color.

    Euclidean distance is calculated between the estimated RGB
    value and every RGB value in the local LEGO color library.
    A smaller distance means the two colors are more similar.

    Args:
        rgb: Estimated red, green, and blue values.

    Returns:
        A tuple containing:
        - the closest LEGO color name
        - the calculated RGB distance
    """
    # Start without a selected color. Infinity guarantees that the first comparison will become the initial closest match.
    closest_color = "Unknown"
    smallest_distance = float("inf")

    # Compare the estimated RGB value against every local reference.
    for color_name, lego_rgb in LEGO_COLORS.items():
        distance = math.sqrt(
            (rgb[0] - lego_rgb[0]) ** 2
            + (rgb[1] - lego_rgb[1]) ** 2
            + (rgb[2] - lego_rgb[2]) ** 2
        )

        # Replace the stored match whenever a smaller distance is found.
        if distance < smallest_distance:
            smallest_distance = distance
            closest_color = color_name

    return closest_color, smallest_distance


# FUNCTION GUIDE: estimate_color
# Purpose: Estimate the average RGB value of the detected LEGO part.
def estimate_color(
    image_path: Path,
    bounding_box: dict,
) -> tuple[int, int, int]:
    """
    Estimate the average RGB value of the detected LEGO part.

    This function is used only when Brickognize does not return a
    color prediction. The Brickognize bounding box is used to crop
    the image so the background has less influence on the estimate.

    Args:
        image_path: Path to the source LEGO image.
        bounding_box: Brickognize bounding-box coordinates.

    Returns:
        A tuple containing the average red, green, and blue values.
    """
    # Open the image safely and convert it to RGB so every pixel consistently contains red, green, and blue channels.
    with Image.open(image_path) as source_image:
        image = source_image.convert("RGB")

        # Extract the Brickognize bounding-box coordinates.
        # Pillow requires integer coordinates for cropping.
        left = int(bounding_box["left"])
        upper = int(bounding_box["upper"])
        right = int(bounding_box["right"])
        lower = int(bounding_box["lower"])

        # Crop the image so the calculation focuses on the detected LEGO part rather than the full photograph.
        image = image.crop((left, upper, right, lower))

        # Resize the crop to standardize the number of pixels analyzed and reduce processing time.
        image = image.resize((50, 50))

        # Retrieve all RGB pixel values from the resized crop.
        pixels = list(image.get_flattened_data())

    if not pixels:
        raise ValueError("No pixels were available for color estimation.")

    # Calculate the average of each color channel separately.
    red_average = sum(pixel[0] for pixel in pixels) // len(pixels)
    green_average = sum(pixel[1] for pixel in pixels) // len(pixels)
    blue_average = sum(pixel[2] for pixel in pixels) // len(pixels)

    return red_average, green_average, blue_average


# ============================================================
# Brickognize Response Helper Functions
# ============================================================

# FUNCTION GUIDE: get_first_detected_item
# Purpose: Return the first detected LEGO item from the API response.
def get_first_detected_item(data: dict) -> dict | None:
    """
    Return the first detected LEGO item from the API response.

    Brickognize may return more than one detected object. This proof
    of concept expects one cropped LEGO part per image, so it uses
    the first detected item.
    """
    detected_items = data.get("detected_items", [])

    if not detected_items:
        return None

    return detected_items[0]


# FUNCTION GUIDE: get_first_bounding_box
# Purpose: Return the first bounding box for a detected LEGO item.
def get_first_bounding_box(detected_item: dict) -> dict | None:
    """
    Return the first bounding box for a detected LEGO item.
    """
    bounding_boxes = detected_item.get("bounding_boxes", [])

    if not bounding_boxes:
        return None

    return bounding_boxes[0]


# FUNCTION GUIDE: get_bricklink_details
# Purpose: Extract the BrickLink part ID and URL from the API response.
def get_bricklink_details(best_match: dict) -> tuple[str, str]:
    """
    Extract the BrickLink part ID and URL from the API response.

    The internal Brickognize endpoint stores catalog-specific
    information inside the candidate item's external_items list.

    Args:
        best_match: Highest-confidence Brickognize part candidate.

    Returns:
        A tuple containing the BrickLink part ID and BrickLink URL.
    """
    external_items = best_match.get("external_items", [])

    # Search the external catalog results for BrickLink.
    for external_item in external_items:
        catalog_name = external_item.get("catalog_name", "").lower()

        if catalog_name == "bricklink":
            part_id = external_item.get("external_id", "Unknown")
            bricklink_url = external_item.get("url", "N/A")
            return part_id, bricklink_url

    # Fall back to Brickognize's internal ID if no BrickLink result exists.
    internal_id = str(best_match.get("id", "Unknown"))
    part_id = internal_id.removeprefix("part-")

    return part_id, "N/A"


# FUNCTION GUIDE: get_bricklink_color_id
# Purpose: Extract the BrickLink color ID from a color candidate.
def get_bricklink_color_id(color_match: dict) -> str:
    """
    Extract the BrickLink color ID from a color candidate.

    The color ID is needed to build the matching BrickLink
    catalog-image URL.
    """
    external_colors = color_match.get("external_colors", [])

    for external_color in external_colors:
        catalog_name = external_color.get(
            "catalog_name",
            "",
        ).lower()

        if catalog_name == "bricklink":
            return str(
                external_color.get("external_id", "")
            )

    return ""


# FUNCTION GUIDE: get_image_mime_type
# Purpose: Return the MIME type needed for the image upload.
def get_image_mime_type(image_path: Path) -> str:
    """
    Return the MIME type needed for the image upload.
    """
    if image_path.suffix.lower() == ".png":
        return "image/png"

    return "image/jpeg"


# FUNCTION GUIDE: save_api_response
# Purpose: Save one complete API response in the shared JSON file.
def save_api_response(
    image_path: Path,
    response_data: dict,
) -> None:
    """
    Save one complete API response in the shared JSON file.

    Each response is stored under its source image filename.
    A repeated filename replaces its earlier response instead
    of creating a duplicate entry.
    """
    API_RESPONSES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    saved_responses = {}

    if (
        API_RESPONSES_FILE.exists()
        and API_RESPONSES_FILE.stat().st_size > 0
    ):
        with API_RESPONSES_FILE.open(
            "r",
            encoding="utf-8",
        ) as json_file:
            saved_responses = json.load(json_file)

        if not isinstance(saved_responses, dict):
            raise ValueError(
                "The shared API response file must contain "
                "a JSON object."
            )

    saved_responses[image_path.name] = response_data

    with API_RESPONSES_FILE.open(
        "w",
        encoding="utf-8",
    ) as json_file:
        json.dump(
            saved_responses,
            json_file,
            indent=4,
        )


# FUNCTION GUIDE: append_result_to_csv
# Purpose: Append one successful result to the shared CSV file.
def append_result_to_csv(result: dict) -> bool:
    """
    Append one successful result to the shared CSV file.

    The image is skipped if its filename already exists in the CSV.

    Returns:
        True if a new row was added.
        False if the image was already recorded.
    """
    RESULTS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    processed_images = set()
    existing_rows = []
    fieldnames = []

    if RESULTS_FILE.exists() and RESULTS_FILE.stat().st_size > 0:
        with RESULTS_FILE.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = list(reader.fieldnames or [])
            existing_rows = list(reader)
            processed_images = {
                row["Image"]
                for row in existing_rows
                if row.get("Image")
            }

    image_name = result.get("Image", "")

    if image_name in processed_images:
        print(
            f"Skipping CSV entry for '{image_name}' "
            "(already recorded)."
        )
        return False

    # Merge new columns into an older CSV header before saving. This keeps a
    # project created by an earlier version readable when new audit fields are
    # introduced later.
    for field_name in result.keys():
        if field_name not in fieldnames:
            fieldnames.append(field_name)

    existing_rows.append(result)
    temporary_file = RESULTS_FILE.with_suffix(".csv.tmp")

    with temporary_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(existing_rows)

    temporary_file.replace(RESULTS_FILE)

    print(
        f"Result for '{image_name}' appended "
        f"to '{RESULTS_FILE}'."
    )

    return True


# ============================================================
# Main Brickognize Processing Function
# ============================================================

# FUNCTION GUIDE: identify_part
# Purpose: Identify one LEGO part and return its part and color information.
def identify_part(image_path: Path) -> dict | None:
    """
    Identify one LEGO part and return its part and color information.

    Brickognize's own color prediction is used whenever available.
    The local Pillow color-estimation method is retained as a fallback.

    Args:
        image_path: Path to one LEGO image.

    Returns:
        A dictionary ready to be written as one CSV row.
        None if the image cannot be processed successfully.
    """
    try:
        # Open the image in binary mode so it can be uploaded as multipart form data.
        with image_path.open("rb") as image_file:
            files = {
                "query_image": (
                    image_path.name,
                    image_file,
                    get_image_mime_type(image_path),
                )
            }

            print(f"Uploading {image_path.name} to Brickognize API...")

            # Send the image to Brickognize and allow up to 30 seconds for the response.
            response = requests.post(
                BRICKOGNIZE_API_URL,
                files=files,
                timeout=30,
            )
            
        print(f"Status Code: {response.status_code}")
        print("\n" + "-" * 60)
        print(f"Image: {image_path.name}")
        print("-" * 60)

        # Stop processing this image if the request was unsuccessful.
        if response.status_code != 200:
            print("\nThe request was not successful.")
            print(response.text)
            return None

        # Convert the JSON response into a Python dictionary.
        data = response.json()

        # Add this complete response to the shared JSON file.
        save_api_response(
            image_path,
            data,
        )

        # Retrieve the first detected LEGO object.
        detected_item = get_first_detected_item(data)

        if detected_item is None:
            print("No detected LEGO items were returned.")
            return None

        # Retrieve the ranked part candidates for the detected object.
        candidate_items = detected_item.get("candidate_items", [])

        if not candidate_items:
            print("No matching LEGO parts were returned.")
            return None

        print("\nSuccess! There is a response from Brickognize API.\n")

        # Brickognize returns candidates from highest to lowest confidence, so the first candidate is used as the best part match.
        best_match = candidate_items[0]

        # Retrieve the detected part's bounding box.
        bounding_box = get_first_bounding_box(detected_item)

        if bounding_box is None:
            print("No bounding box was returned for the detected part.")
            return None

        # Convert the bounding-box score into a percentage.
        bounding_box_score = bounding_box.get("score", 0) * 100

        # ------------------------------------------------------------
        # Color Processing
        # ------------------------------------------------------------

        candidate_colors = best_match.get("candidate_colors", [])

        if candidate_colors:
            # Use Brickognize's highest-confidence color candidate.
            # Returns color candidates in confidence order, so the first result is used as the best color match.
            best_color_match = candidate_colors[0]

            bricklink_color_id = get_bricklink_color_id(
                best_color_match
            )

            lego_color = best_color_match.get("name", "Unknown")
            color_confidence = best_color_match.get("score", 0) * 100

            # Flag low-confidence Brickognize color predictions for review.
            color_review_required = (
                "Yes"
                if color_confidence < COLOR_REVIEW_THRESHOLD
                else "No"
            )

            color_method = "Brickognize Color Prediction"

            # These local-estimation values are not needed when the Brickognize color prediction is available.
            estimated_rgb = ""
            color_distance = ""

        else:
            # Use the Pillow-based method only when Brickognize does not provide a color result.
            estimated_rgb = estimate_color(image_path, bounding_box)

            lego_color, color_distance = get_closest_lego_color(
                estimated_rgb
            )
            # The fallback still reports the matching BrickLink color ID
            # whenever that locally estimated color is in the verified map.
            bricklink_color_id = BRICKLINK_REVIEW_COLORS.get(
                lego_color,
                "",
            )

            # Convert RGB distance into an experimental fallback score.
            # Smaller distances produce higher scores.
            color_confidence = max(
                0.0,
                100.0 - color_distance / 3,
            )

            color_review_required = (
                "Yes"
                if color_confidence < COLOR_REVIEW_THRESHOLD
                else "No"
            )
        
            color_method = "Average RGB Fallback"

        # ------------------------------------------------------------
        # Part Processing
        # ------------------------------------------------------------

        # Retrieve the BrickLink part ID and URL.
        part_id, bricklink_url = get_bricklink_details(best_match)


        if bricklink_color_id:
            reference_image_url = (
                "https://img.bricklink.com/ItemImage/PN/"
                f"{bricklink_color_id}/{part_id}.png"
            )
        else:
            reference_image_url = (
                "https://img.bricklink.com/ItemImage/P/"
                f"{part_id}.png"
            )

        # Open the BrickLink catalog page with the predicted color selected.
        if bricklink_url != "N/A" and bricklink_color_id:
            bricklink_url = f"{bricklink_url}&idColor={bricklink_color_id}"

        # Extract the remaining useful part details.
        response_id = data.get("id", "Unknown")

        
        # Extract the remaining useful part details.
        response_id = data.get("id", "Unknown")
        part_name = best_match.get("name", "Unknown")
        item_type = best_match.get("type", "Unknown")

        # Convert the Brickognize part score into a percentage.
        part_confidence = best_match.get("score", 0) * 100

        # Flag lower-confidence part predictions for review.
        part_review_required = (
            "Yes"
            if part_confidence < PART_REVIEW_THRESHOLD
            else "No"
        )

        # ------------------------------------------------------------
        # Terminal Output
        # ------------------------------------------------------------

        print(f"LEGO Color: {lego_color}")
        print(f"Color Confidence: {color_confidence:.2f}%")
        print(f"Color Review Required: {color_review_required}")
        print(f"Color Method: {color_method}")
        print(f"Part ID: {part_id}")
        print(f"Part Name: {part_name}")
        print(f"Type: {item_type}")
        print(f"Part Confidence: {part_confidence:.2f}%")
        print(f"Part Review Required: {part_review_required}")
        print(f"Reference Image: {reference_image_url}")
        print(f"BrickLink: {bricklink_url}")
        print("-" * 60 + "\n")

        # ------------------------------------------------------------
        # CSV Row
        # ------------------------------------------------------------

        # Return one complete dictionary. The dictionary keys become the CSV column headers and the values become one CSV row.
        return {
            "Image": image_path.name,
            "Response ID": response_id,
            "Part ID": part_id,
            "Part Name": part_name,
            "Type": item_type,
            "Part Confidence": f"{part_confidence:.2f}%",
            "Part Review Required": part_review_required,
            "Part Approval Status": (
                "Pending Review"
                if part_review_required == "Yes"
                else "Auto-Accepted"
            ),
            "LEGO Color": lego_color,
            "BrickLink Color ID": bricklink_color_id,
            "Predicted LEGO Color": lego_color,
            "Predicted BrickLink Color ID": bricklink_color_id,
            "Color Confidence": f"{color_confidence:.2f}%",
            "Color Review Required": color_review_required,
            "Color Approval Status": (
                "Pending Review"
                if color_review_required == "Yes"
                else "Auto-Accepted"
            ),
            "Color Method": color_method,
            "Estimated RGB": estimated_rgb,
            "Color Distance": color_distance,
            "Bounding Box Score": f"{bounding_box_score:.2f}%",
            "Reference Image URL": reference_image_url,
            "BrickLink URL": bricklink_url,
            # Final fields initially mirror the prediction. Employee review
            # updates these fields while the prediction remains unchanged.
            "Final Review Status": (
                "Needs Part and Color Review"
                if part_review_required == "Yes"
                and color_review_required == "Yes"
                else "Needs Part Review"
                if part_review_required == "Yes"
                else "Needs Color Review"
                if color_review_required == "Yes"
                else "Ready"
            ),
            "Final Part ID": part_id,
            "Final Part Name": part_name,
            "Final Part Confidence": f"{part_confidence:.2f}%",
            "Final LEGO Color": lego_color,
            "Final BrickLink Color ID": bricklink_color_id,
            "Part Review Completed": "No",
            "Color Review Completed": "No",
            "Final BrickLink URL": bricklink_url,
        }

    # Handle a missing or incorrectly named source image.
    except FileNotFoundError:
        print(
            f"Error: The file '{image_path}' was not found. "
            "Please check the path and try again."
        )

    # Handle requests that take longer than the allowed timeout.
    except requests.Timeout:
        print("Error: The Brickognize request timed out.")

    # Handle other network and HTTP request errors.
    except requests.RequestException as error:
        print(f"Request error: {error}")

    # Handle unexpected changes or missing fields in the API response.
    except (KeyError, TypeError, IndexError) as error:
        print(f"Error reading the Brickognize response structure: {error}")

    # Handle invalid JSON or local color-processing problems.
    except ValueError as error:
        print(f"Data-processing error: {error}")

    # Catch any remaining unexpected errors and print a readable message.
    except Exception as error:
        print(f"An unexpected error occurred: {error}")

    return None


# ============================================================
# Main Program
# ============================================================

if __name__ == "__main__":
    # Create output folders automatically if they do not exist.
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    API_RESPONSES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Read the existing CSV and remember filenames that have already been processed. A set makes duplicate checks quick.
    processed_images: set[str] = set()

    if RESULTS_FILE.exists() and RESULTS_FILE.stat().st_size > 0:
        with RESULTS_FILE.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as csv_file:
            reader = csv.DictReader(csv_file)

            processed_images = {
                row["Image"]
                for row in reader
                if row.get("Image")
            }

    # Collect common JPEG and PNG image types and process them in alphabetical filename order.
    image_files = sorted(
        [
            *IMAGES_FOLDER.glob("*.jpg"),
            *IMAGES_FOLDER.glob("*.jpeg"),
            *IMAGES_FOLDER.glob("*.png"),
        ]
    )

    if not image_files:
        print("No JPEG or PNG images found in the 'images' folder.")

    else:
        print(f"Found {len(image_files)} image(s) in the 'images' folder.")


        # Process every image individually.
        for image_file in image_files:
            # Skip filenames that are already recorded in the CSV.
            if image_file.name in processed_images:
                print(
                    f"Skipping {image_file.name} "
                    "(already processed)."
                )
                continue

            result = identify_part(image_file)

            # Do not write a CSV row when the image failed.
            if result is None:
                continue

            # Save the successful result through the shared backend CSV function.
            append_result_to_csv(result)

            # Remember the filename during the current run as well.
            processed_images.add(image_file.name)

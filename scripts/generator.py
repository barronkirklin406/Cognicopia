"""Watch payloads/ and render incoming Cognicopia payloads as PDFs."""

from __future__ import annotations

import json
import hashlib
import logging
import math
import random
import sys
import threading
import time
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from fpdf import FPDF
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

BASE_DIR = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = BASE_DIR / "payloads"
OUTPUT_DIR = BASE_DIR / "output_pdfs"
PAGE_WIDTH_MM = 150
PAGE_HEIGHT_MM = 200
ACTIVITY_LABELS = {
    "word_search": "Word Search",
    "number_match": "Number Match",
    "reminiscence_prompts": "Reminiscence Prompts",
    "dots": "Connect the Dots",
    "color": "Coloring Page",
    "mandala": "Color the Pattern",
}
ACTIVITY_INSTRUCTIONS = {
    "word_search": "Find and circle the words connected to this resident.",
    "number_match": "Draw a line between each number and its matching quantity.",
    "reminiscence_prompts": "Use these prompts to invite a gentle conversation.",
    "dots": "Connect the numbered dots to reveal the hidden shape.",
    "color": "Add color to this original line-art pattern.",
    "mandala": "Color the repeating pattern at your own pace.",
}
ACTIVITY_ALIASES = {
    "wordsearch": "word_search",
    "word_search": "word_search",
    "talk": "reminiscence_prompts",
    "lifestory": "reminiscence_prompts",
    "colornum": "color",
    "color": "color",
    "mandala": "mandala",
    "dots": "dots",
    "number_match": "number_match",
    "reminiscence_prompts": "reminiscence_prompts",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("cognicopia-generator")


def display_value(value: Any) -> str:
    """Convert nested payload values into readable PDF text."""
    if isinstance(value, list):
        return ", ".join(display_value(item) for item in value) or "None"
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {display_value(item)}" for key, item in value.items()
        ) or "None"
    if value is None or value == "":
        return "None"
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "replace").decode("ascii")


def validate_payload(payload: Any) -> dict[str, Any]:
    """Validate the minimum contract before any PDF or ZIP work begins."""
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")
    profile = payload.get("profile", payload)
    if not isinstance(profile, dict):
        raise ValueError("payload.profile must be an object")
    fields = profile.get("fields", {})
    if fields is not None and not isinstance(fields, dict):
        raise ValueError("payload.profile.fields must be an object")
    preferences = payload.get("packetPreferences", {})
    if preferences is not None and not isinstance(preferences, dict):
        raise ValueError("payload.packetPreferences must be an object")
    selected = preferences.get("selectedActivities", []) if isinstance(preferences, dict) else []
    if selected is not None and not isinstance(selected, list):
        raise ValueError("packetPreferences.selectedActivities must be an array")
    if isinstance(selected, list) and any(not isinstance(item, str) for item in selected):
        raise ValueError("packetPreferences.selectedActivities must contain strings")
    return payload


def add_thumb_tab(pdf: FPDF, label: str, fill_color: tuple[int, int, int] = (32, 67, 72)) -> None:
    """Draw a readable page marker in the outer right margin."""
    current_x, current_y = pdf.get_x(), pdf.get_y()
    tab_width, tab_height = 12, 22
    tab_x, tab_y = PAGE_WIDTH_MM - tab_width, 18
    pdf.set_fill_color(*fill_color)
    pdf.set_draw_color(255, 255, 255)
    pdf.set_line_width(0.3)
    pdf.rect(tab_x, tab_y, tab_width, tab_height, style="DF")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 6.2)
    pdf.set_xy(tab_x + 1, tab_y + 3)
    pdf.multi_cell(tab_width - 2, 3.2, label, align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(current_x, current_y)


def thumb_label(activity_id: str) -> str:
    return {
        "word_search": "Puzzles",
        "number_match": "Puzzles",
        "reminiscence_prompts": "Reminiscence",
        "dots": "Puzzles",
        "color": "Coloring",
        "mandala": "Coloring",
    }.get(activity_id, "Activity")


def add_section(pdf: FPDF, heading: str, values: dict[str, Any]) -> None:
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, heading)
    pdf.ln(9)
    pdf.set_font("Helvetica", size=10)
    for key, value in values.items():
        label = key.replace("_", " ").capitalize()
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(43, 6, f"{label}:")
        pdf.set_font("Helvetica", size=10)
        pdf.multi_cell(pdf.epw - 43, 6, display_value(value))
    pdf.ln(3)


def accessibility_metadata(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, bool]]:
    institutional = payload.get("institutional", {})
    if not isinstance(institutional, dict):
        institutional = {}
    accessibility = payload.get("accessibility", {})
    if not isinstance(accessibility, dict):
        accessibility = {}

    institutional_values = {
        "partner": str(
            institutional.get("partnerUniversity")
            or institutional.get("institutionalPartner")
            or "Not specified"
        ),
        "sponsor": str(
            institutional.get("researchGroupFacilitySponsor")
            or institutional.get("researchSponsor")
            or "Not specified"
        ),
    }
    accessibility_values = {
        "high_contrast": bool(accessibility.get("highContrast")),
        "extra_large_print": bool(accessibility.get("extraLargePrint")),
        "simplified_layout": bool(accessibility.get("simplifiedLayout")),
    }
    return institutional_values, accessibility_values


def add_accessibility_design_profile(
    pdf: FPDF,
    institutional: dict[str, str],
    accessibility: dict[str, bool],
) -> None:
    """Render the active design settings transparently on the cover."""
    x, y, width, height = pdf.l_margin, pdf.get_y(), pdf.epw, 45
    pdf.set_fill_color(235, 246, 246)
    pdf.set_draw_color(40, 110, 117)
    pdf.set_line_width(0.6)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 5, y + 4)
    pdf.set_text_color(26, 80, 88)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(width - 10, 6, "UNIVERSAL DESIGN & ACCESSIBILITY SPECIFICATION")
    pdf.set_xy(x + 5, y + 11)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", size=8.5)
    pdf.cell(width - 10, 5, "Active design settings recorded for institutional pilot review")

    pdf.set_xy(x + 5, y + 18)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(27, 5, "Partner:")
    pdf.set_font("Helvetica", size=8.5)
    pdf.cell(width - 37, 5, institutional["partner"][:82])
    pdf.set_xy(x + 5, y + 24)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(27, 5, "Sponsor:")
    pdf.set_font("Helvetica", size=8.5)
    pdf.cell(width - 37, 5, institutional["sponsor"][:82])

    contrast = (
        "High Contrast active; 21:1 black/white treatment"
        if accessibility["high_contrast"]
        else "Standard palette; WCAG AA 4.5:1 body-text target"
    )
    typography = (
        "Extra-Large Print active; 1.25x typography"
        if accessibility["extra_large_print"]
        else "Standard typography scale; 1.0x"
    )
    pacing = (
        "Simplified Layout active; reduced-clutter pacing"
        if accessibility["simplified_layout"]
        else "Standard cognitive pacing"
    )
    pdf.set_xy(x + 5, y + 31)
    pdf.set_font("Helvetica", size=7.7)
    pdf.multi_cell(
        width - 10,
        4.5,
        f"Contrast: {contrast}\nTypography: {typography}\nPacing: {pacing}\n"
        "Review note: descriptive configuration only; no external certification claimed.",
    )
    pdf.set_y(y + height + 6)


def activity_selection(payload: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], int]:
    preferences = payload.get("packetPreferences", {})
    if not isinstance(preferences, dict):
        preferences = {}

    selected = preferences.get("selectedActivities", [])
    if not isinstance(selected, list):
        selected = []
    selected = [ACTIVITY_ALIASES[item] for item in selected if item in ACTIVITY_ALIASES]

    if not selected:
        old_activities = profile.get("acts", {})
        if isinstance(old_activities, dict):
            selected = [ACTIVITY_ALIASES[item] for item, enabled in old_activities.items()
                        if enabled and item in ACTIVITY_ALIASES]
    if not selected:
        selected = ["reminiscence_prompts"]

    try:
        target_pages = int(preferences.get("targetPages", len(selected) + 1))
    except (TypeError, ValueError):
        target_pages = len(selected) + 1
    return selected, max(2, min(30, target_pages))


def activity_data(
    activity_id: str,
    fields: dict[str, Any],
    occurrence: int,
    generation_seed: str = "",
) -> dict[str, Any]:
    """Build deterministic puzzle content shared by the packet and key."""
    seed_text = "|".join(str(fields.get(key, "")) for key in ("first", "town", "job", "hobbies", "favs"))
    seed = int(hashlib.sha256(f"{generation_seed}|{seed_text}|{activity_id}|{occurrence}".encode()).hexdigest()[:8], 16)

    if activity_id == "word_search":
        raw_words = []
        for key in ("first", "town", "job", "hobbies", "favs"):
            value = str(fields.get(key, ""))
            raw_words.extend(value.replace(",", " ").split())
        words = []
        for word in raw_words:
            clean = "".join(char for char in word.upper() if "A" <= char <= "Z")[:10]
            if len(clean) >= 2 and clean not in words:
                words.append(clean)
        words = words[:8] or ["MEMORY", "FAMILY"]
        size = 10
        grid = [["" for _ in range(size)] for _ in range(size)]
        solutions = []
        for row, word in enumerate(words):
            start = (seed + row * 3) % (size - len(word) + 1)
            for column, char in enumerate(word):
                grid[row][start + column] = char
            solutions.append({"word": word, "start": [row, start], "end": [row, start + len(word) - 1]})
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for row in range(size):
            for column in range(size):
                if not grid[row][column]:
                    grid[row][column] = alphabet[(seed + row * size + column) % len(alphabet)]
        return {"words": words, "grid": grid, "solutions": solutions}

    if activity_id == "number_match":
        pairs = [{"number": number, "quantity": number} for number in range(1, 7)]
        right_order = sorted(pairs, key=lambda pair: (seed + pair["quantity"] * 17) % 101)
        return {"pairs": pairs, "right_order": [pair["quantity"] for pair in right_order]}

    rng = random.Random(seed)
    if activity_id == "dots":
        shape = rng.choice(("star", "house", "leaf", "fish", "flower"))
        points = []
        if shape == "star":
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                radius = 34 if index % 2 == 0 else 15
                points.append([75 + math.cos(angle) * radius, 112 + math.sin(angle) * radius])
        elif shape == "house":
            points = [[45, 125], [45, 85], [75, 58], [105, 85], [105, 125], [45, 125]]
        elif shape == "leaf":
            for index in range(16):
                angle = math.pi * index / 15
                points.append([75 + math.cos(angle) * 42, 105 - math.sin(angle) * 30])
            points += [[75, 105], [75, 145]]
        elif shape == "fish":
            points = [[35, 105], [52, 82], [88, 82], [112, 65], [105, 103], [112, 140], [88, 122], [52, 122], [35, 105]]
        else:
            for index in range(16):
                angle = index * math.pi / 8
                radius = 34 if index % 2 == 0 else 17
                points.append([75 + math.cos(angle) * radius, 105 + math.sin(angle) * radius])
        jittered = [[round(x + rng.uniform(-2.5, 2.5), 1), round(y + rng.uniform(-2.5, 2.5), 1)] for x, y in points]
        return {"shape": shape, "points": jittered}

    if activity_id in ("color", "mandala"):
        pattern = rng.choice(("rosette", "waves", "petals", "tessellation"))
        rings = rng.randint(3, 5)
        spokes = rng.choice((6, 8, 10, 12))
        hobby = str(fields.get("hobbies") or fields.get("favs") or "personal")
        return {"pattern": pattern, "rings": rings, "spokes": spokes, "theme": hobby[:28]}

    return {
        "prompts": [
            "A place I remember clearly is",
            "A favorite family tradition was",
            "Something I enjoyed doing was",
            "A person who mattered to me was",
        ],
        "solutions": "Open response; there is no single correct answer.",
    }


def draw_polyline(pdf: FPDF, points: list[list[float]], close: bool = False) -> None:
    path = points + ([points[0]] if close and points else [])
    for start, end in zip(path, path[1:]):
        pdf.line(start[0], start[1], end[0], end[1])


def draw_rotated_ellipse(
    pdf: FPDF, center_x: float, center_y: float, radius_x: float, radius_y: float, angle: float
) -> None:
    points = []
    for index in range(25):
        theta = (math.pi * 2 * index) / 24
        x = math.cos(theta) * radius_x
        y = math.sin(theta) * radius_y
        points.append([
            center_x + x * math.cos(angle) - y * math.sin(angle),
            center_y + x * math.sin(angle) + y * math.cos(angle),
        ])
    draw_polyline(pdf, points, close=True)


def draw_coloring_pattern(pdf: FPDF, data: dict[str, Any], center_y: float = 112) -> None:
    center_x = 75
    rings = data["rings"]
    spokes = data["spokes"]
    pattern = data["pattern"]
    pdf.set_line_width(0.45)
    for ring in range(1, rings + 1):
        radius = 12 + ring * 14
        pdf.ellipse(center_x - radius, center_y - radius, radius * 2, radius * 2)

    for spoke in range(spokes):
        angle = (math.pi * 2 * spoke) / spokes
        outer_x = center_x + math.cos(angle) * 68
        outer_y = center_y + math.sin(angle) * 68
        pdf.line(center_x, center_y, outer_x, outer_y)

    if pattern == "rosette":
        for spoke in range(spokes):
            angle = (math.pi * 2 * spoke) / spokes
            x = center_x + math.cos(angle) * 34
            y = center_y + math.sin(angle) * 34
            draw_rotated_ellipse(pdf, x, y, 10, 18, angle)
    elif pattern == "waves":
        for row in range(5):
            points = []
            for column in range(13):
                x = 18 + column * 9
                y = 55 + row * 26 + math.sin(column * math.pi / 2) * 5
                points.append([x, y])
            draw_polyline(pdf, points)
    elif pattern == "petals":
        for spoke in range(spokes):
            angle = (math.pi * 2 * spoke) / spokes
            x = center_x + math.cos(angle) * 43
            y = center_y + math.sin(angle) * 43
            draw_rotated_ellipse(pdf, x, y, 13, 8, angle)
    else:
        for row in range(5):
            for column in range(5):
                x = 31 + column * 22
                y = 68 + row * 22
                pdf.rect(x, y, 18, 18)
                pdf.line(x, y, x + 18, y + 18)
                pdf.line(x + 18, y, x, y + 18)


def add_activity_page(
    pdf: FPDF,
    activity_id: str,
    page_number: int,
    fields: dict[str, Any],
    data: dict[str, Any],
) -> None:
    label = ACTIVITY_LABELS[activity_id]
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)
    add_thumb_tab(pdf, thumb_label(activity_id))
    pdf.set_fill_color(32, 67, 72)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, label, fill=True)
    pdf.ln(16)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(0, 8, f"Page {page_number}: {label}")
    pdf.ln(2)
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 7, ACTIVITY_INSTRUCTIONS[activity_id])
    pdf.ln(8)

    resident_name = display_value(fields.get("goes") or fields.get("first") or "Resident")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Made for {resident_name}")
    pdf.ln(12)

    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    if activity_id == "word_search":
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Words to find")
        pdf.ln(10)
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, "   ".join(data["words"]))
        pdf.ln(10)
        pdf.set_font("Courier", size=11)
        for row in data["grid"]:
            pdf.cell(0, 7, " ".join(row), align="C")
            pdf.ln(7)
    elif activity_id == "number_match":
        pdf.set_font("Helvetica", size=13)
        pdf.cell(35, 8, "Numbers", border=1, align="C")
        pdf.cell(80, 8, "Quantities", border=1, align="C")
        pdf.ln(10)
        for number, quantity in zip(range(1, 7), data["right_order"]):
            pdf.cell(35, 12, str(number), border=1, align="C")
            pdf.cell(80, 12, "o " * quantity, border=1, align="C")
            pdf.ln(14)
    elif activity_id == "dots":
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(0, 6, f"Shape: {data['shape'].capitalize()}   Start at dot 1")
        pdf.set_line_width(0.6)
        for index, point in enumerate(data["points"], start=1):
            point_x, point_y = point[0], point[1] + 28
            pdf.ellipse(point_x - 1.4, point_y - 1.4, 2.8, 2.8)
            pdf.set_xy(point_x + 2, point_y - 3)
            pdf.cell(8, 6, str(index))
    elif activity_id in ("color", "mandala"):
        pdf.set_font("Helvetica", size=10)
        pdf.multi_cell(0, 6, f"Pattern inspired by: {data['theme']}")
        draw_coloring_pattern(pdf, data, center_y=min(max(pdf.get_y() + 28, 104), 112))
    else:
        pdf.set_font("Helvetica", "B", 12)
        for prompt in data["prompts"]:
            pdf.multi_cell(0, 8, prompt)
            pdf.line(pdf.l_margin, pdf.get_y() + 3, pdf.w - pdf.r_margin, pdf.get_y() + 3)
            pdf.ln(14)
    pdf.set_auto_page_break(auto=True, margin=12)


def add_answer_page(
    pdf: FPDF,
    activity_id: str,
    page_number: int,
    data: dict[str, Any],
) -> None:
    """Render the solution page for one generated activity."""
    label = ACTIVITY_LABELS[activity_id]
    pdf.add_page()
    add_thumb_tab(pdf, "Answers", (92, 54, 38))
    pdf.set_fill_color(92, 54, 38)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, f"Answer Key: {label}", fill=True)
    pdf.ln(16)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"Page {page_number} solution")
    pdf.ln(12)
    pdf.set_font("Helvetica", size=11)
    if activity_id == "word_search":
        pdf.multi_cell(0, 7, "Word locations use zero-based row and column coordinates.")
        pdf.ln(4)
        for solution in data["solutions"]:
            pdf.cell(0, 7, f"{solution['word']}: {solution['start']} to {solution['end']}")
            pdf.ln(7)
    elif activity_id == "number_match":
        for pair in data["pairs"]:
            pdf.cell(0, 8, f"{pair['number']} matches {pair['quantity']} circle(s)")
            pdf.ln(8)
    elif activity_id == "dots":
        pdf.multi_cell(0, 8, f"Connect dots 1 through {len(data['points'])} to reveal a {data['shape']}.")
        pdf.ln(4)
        for index, point in enumerate(data["points"], start=1):
            pdf.cell(0, 7, f"{index}: ({point[0]}, {point[1]})")
            pdf.ln(7)
    elif activity_id in ("color", "mandala"):
        pdf.multi_cell(
            0,
            8,
            f"Pattern: {data['pattern']}; rings: {data['rings']}; spokes: {data['spokes']}. "
            "There is no single correct coloring solution.",
        )
    else:
        pdf.multi_cell(0, 8, data["solutions"])


def pilot_logging_enabled(payload: dict[str, Any]) -> bool:
    """Read pilot logging from the supported payload configuration shapes."""
    value = payload.get("pilotTrialLogging")
    if value is None:
        value = payload.get("pilot_trial_logging")
    if value is None:
        value = payload.get("trialLogging")
    if value is None:
        value = payload.get("pilotLoggingEnabled")
    if value is None and isinstance(payload.get("packetPreferences"), dict):
        value = payload["packetPreferences"].get("pilotTrialLogging")

    if isinstance(value, dict):
        value = value.get("enabled", value.get("active", False))
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def add_rating_row(pdf: FPDF, label: str) -> None:
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(48, 9, label)
    pdf.set_font("Helvetica", size=8)
    for rating in range(1, 6):
        pdf.cell(12, 9, str(rating), border=1, align="C")
    pdf.cell(0, 9, "  1 = low   5 = high", align="L")
    pdf.ln(11)


def add_pilot_trial_log_page(
    pdf: FPDF,
    fields: dict[str, Any],
    institutional: dict[str, str],
) -> None:
    """Append a caregiver-facing evaluation page to the participant packet."""
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)
    add_thumb_tab(pdf, "Trial Log", (92, 54, 38))
    pdf.set_fill_color(32, 67, 72)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 11, "Pilot Trial Evaluation & Caregiver Feedback Log", fill=True)
    pdf.ln(15)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", size=8.5)
    resident = display_value(fields.get("goes") or fields.get("first") or "Resident")
    pdf.cell(0, 5, f"Participant: {resident}")
    pdf.ln(6)
    pdf.cell(0, 5, f"Partner: {institutional['partner']}   Sponsor: {institutional['sponsor']}")
    pdf.ln(9)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Trial metrics")
    pdf.ln(9)
    pdf.set_draw_color(32, 67, 72)
    pdf.set_line_width(0.4)
    add_rating_row(pdf, "Participant engagement")
    add_rating_row(pdf, "Mood shift")
    add_rating_row(pdf, "Task completion pacing")
    add_rating_row(pdf, "Caregiver ease of use")

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Qualitative notes")
    pdf.ln(9)
    pdf.set_line_width(0.35)
    for _ in range(4):
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(8)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(32, 7, "Next step:")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(58, 7, "", border="B")
    pdf.cell(24, 7, "Caregiver:")
    pdf.cell(0, 7, "", border="B")
    pdf.ln(11)
    pdf.cell(32, 7, "Date:")
    pdf.cell(35, 7, "", border="B")
    pdf.cell(22, 7, "Session:")
    pdf.cell(0, 7, "", border="B")


def render_caregiver_trial_log(
    fields: dict[str, Any],
    institutional: dict[str, str],
    output_path: Path,
) -> None:
    """Write the caregiver log as a standalone PDF for institutional packaging."""
    log_pdf = FPDF(orientation="P", unit="mm", format=(PAGE_WIDTH_MM, PAGE_HEIGHT_MM))
    log_pdf.set_auto_page_break(auto=True, margin=12)
    log_pdf.set_margins(12, 12, 12)
    add_pilot_trial_log_page(log_pdf, fields, institutional)
    log_pdf.output(str(output_path))


def safe_package_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "_" for char in value)
    return "_".join(cleaned.split())[:80] or "Institution"


def create_institutional_package(
    payload: dict[str, Any],
    fields: dict[str, Any],
    institutional: dict[str, str],
    workbook_path: Path,
    answer_key_path: Path,
) -> Path:
    """Create one ZIP containing the generated institutional pilot documents."""
    facility = str(fields.get("fac") or "").strip()
    partner = institutional["partner"]
    package_owner = facility if facility and facility != "Not specified" else partner
    package_date = date.today().isoformat()
    package_id = safe_package_name(workbook_path.stem)
    package_name = (
        f"{safe_package_name(package_owner)}_Cognicopia_Pilot_Package_"
        f"{package_date}_{package_id}.zip"
    )
    package_path = workbook_path.parent / package_name

    package_files = [workbook_path, answer_key_path]
    if pilot_logging_enabled(payload):
        log_path = workbook_path.with_name(f"{workbook_path.stem}_caregiver_trial_log.pdf")
        render_caregiver_trial_log(fields, institutional, log_path)
        package_files.append(log_path)

    temporary_package = package_path.with_suffix(package_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for document in package_files:
                archive.write(document, arcname=document.name)
        temporary_package.replace(package_path)
    finally:
        temporary_package.unlink(missing_ok=True)
    return package_path


def render_payload(payload: dict[str, Any], output_path: Path) -> None:
    """Render a cover and selected activity pages using fixed 3:4 pages."""
    validate_payload(payload)
    profile = payload.get("profile", payload)
    if not isinstance(profile, dict):
        raise ValueError("payload.profile must be an object")

    fields = profile.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}

    pdf = FPDF(orientation="P", unit="mm", format=(PAGE_WIDTH_MM, PAGE_HEIGHT_MM))
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    selected, target_pages = activity_selection(payload, profile)
    pdf.add_page()
    add_thumb_tab(pdf, "Cover")

    pdf.set_fill_color(32, 67, 72)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "Cognicopia Activity Packet", fill=True)
    pdf.ln(16)

    pdf.set_text_color(0, 0, 0)
    first_name = fields.get("first") or "Resident"
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 9, f"Personalized packet for {display_value(first_name)}")
    pdf.ln(3)

    institutional, accessibility = accessibility_metadata(payload)
    add_accessibility_design_profile(pdf, institutional, accessibility)

    exported_at = payload.get("exportedAt")
    if exported_at:
        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, f"Exported: {display_value(exported_at)}")
        pdf.ln(10)
        pdf.set_text_color(0, 0, 0)

    add_section(pdf, "Resident", fields)
    add_section(pdf, "Workbook", {"pages": target_pages, "activities": selected})

    activity_pages = target_pages - 1
    activity_data_by_page = []
    generation_seed = str(payload.get("exportedAt", output_path.stem))
    for index in range(activity_pages):
        activity_id = selected[index % len(selected)]
        data = activity_data(
            activity_id,
            fields,
            index // len(selected),
            generation_seed,
        )
        activity_data_by_page.append((activity_id, data))
        add_activity_page(pdf, activity_id, index + 2, fields, data)

    if pilot_logging_enabled(payload):
        add_pilot_trial_log_page(pdf, fields, institutional)

    pdf.output(str(output_path))

    answer_key_path = output_path.with_name(f"{output_path.stem}_answer_key.pdf")
    answer_key = FPDF(
        orientation="P", unit="mm", format=(PAGE_WIDTH_MM, PAGE_HEIGHT_MM)
    )
    answer_key.set_auto_page_break(auto=True, margin=12)
    answer_key.set_margins(12, 12, 12)
    answer_key.add_page()
    answer_key.set_fill_color(92, 54, 38)
    answer_key.set_text_color(255, 255, 255)
    answer_key.set_font("Helvetica", "B", 20)
    answer_key.cell(0, 12, "Cognicopia Answer Key", fill=True)
    answer_key.ln(16)
    answer_key.set_text_color(0, 0, 0)
    answer_key.set_font("Helvetica", "B", 16)
    answer_key.multi_cell(0, 9, f"Solutions for {display_value(first_name)}")
    answer_key.ln(4)
    answer_key.set_font("Helvetica", size=10)
    answer_key.multi_cell(
        0,
        6,
        "Keep this companion document with staff materials. It follows the activity page order.",
    )
    for index, (activity_id, data) in enumerate(activity_data_by_page):
        add_answer_page(answer_key, activity_id, index + 2, data)
    answer_key.output(str(answer_key_path))
    package_path = create_institutional_package(
        payload, fields, institutional, output_path, answer_key_path
    )
    LOGGER.info("Created institutional package %s", package_path.name)


def process_payload(payload_path: Path) -> None:
    if payload_path.suffix.lower() != ".json":
        return

    try:
        payload = None
        for attempt in range(4):
            try:
                with payload_path.open("r", encoding="utf-8") as payload_file:
                    payload = json.load(payload_file)
                break
            except (OSError, json.JSONDecodeError):
                if attempt == 3:
                    raise
                time.sleep(0.15 * (attempt + 1))
        validate_payload(payload)
        output_path = OUTPUT_DIR / f"{payload_path.stem}.pdf"
        render_payload(payload, output_path)
        LOGGER.info(
            "Rendered %s and %s",
            output_path.name,
            output_path.with_name(f"{output_path.stem}_answer_key.pdf").name,
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError, RuntimeError) as error:
        LOGGER.error("Could not render %s: %s", payload_path.name, error)
    except Exception:
        LOGGER.exception("Unexpected error while rendering %s", payload_path.name)


class PayloadHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule(self, path: Path) -> None:
        key = str(path.resolve())
        with self._lock:
            previous = self._timers.pop(key, None)
            if previous:
                previous.cancel()
            timer = threading.Timer(0.5, self._process, args=(path, key))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _process(self, path: Path, key: str) -> None:
        try:
            process_payload(path)
        finally:
            with self._lock:
                self._timers.pop(key, None)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(Path(event.src_path))


def main() -> None:
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    observer = Observer()
    observer.schedule(PayloadHandler(), str(PAYLOAD_DIR), recursive=False)
    observer.start()
    LOGGER.info("Watching %s for JSON payloads", PAYLOAD_DIR)
    try:
        observer.join()
    except KeyboardInterrupt:
        LOGGER.info("Stopping generator")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    try:
        main()
    except ImportError as error:
        LOGGER.error("Install the required packages with: py -m pip install watchdog fpdf2")
        LOGGER.error("Missing dependency: %s", error)
        sys.exit(1)

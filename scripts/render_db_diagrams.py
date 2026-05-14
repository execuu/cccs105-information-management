from __future__ import annotations

import html
import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.db import models


django.setup()

from django.contrib.auth.models import User

from attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassSection,
    Classroom,
    Enrollment,
    InstructorProfile,
    ScanEvent,
    Student,
)


DIAGRAM_DIR = BASE_DIR / "docs" / "diagrams"

MODEL_ORDER = [
    User,
    InstructorProfile,
    Classroom,
    Student,
    ClassSection,
    Enrollment,
    AttendanceSession,
    ScanEvent,
    AttendanceRecord,
]

ENTITY_NAMES = {
    User: "auth_user",
    InstructorProfile: "InstructorProfile",
    Classroom: "Classroom",
    Student: "Student",
    ClassSection: "ClassSection",
    Enrollment: "Enrollment",
    AttendanceSession: "AttendanceSession",
    ScanEvent: "ScanEvent",
    AttendanceRecord: "AttendanceRecord",
}


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def table_name(model: type[models.Model]) -> str:
    return model._meta.db_table


def column_for(model: type[models.Model], field_name: str) -> str:
    return model._meta.get_field(field_name).column


def field_label(field: models.Field) -> str:
    bits = [field.column]
    markers: list[str] = []
    if field.primary_key:
        markers.append("PK")
    if field.is_relation and getattr(field, "many_to_one", False):
        markers.append("FK")
    if field.is_relation and getattr(field, "one_to_one", False):
        markers.append("FK")
    if getattr(field, "unique", False) and not field.primary_key:
        markers.append("UNIQUE")
    if getattr(field, "null", False):
        markers.append("NULL")
    if isinstance(field, models.JSONField):
        markers.append("JSON")
    if (
        getattr(field, "db_index", False)
        and not getattr(field, "unique", False)
        and not field.is_relation
    ):
        markers.append("INDEX")
    if markers:
        bits.append(" ".join(markers))
    return " ".join(bits)


def model_lines(model: type[models.Model]) -> list[str]:
    return [field_label(field) for field in model._meta.local_fields]


def unique_constraints(model: type[models.Model]) -> list[str]:
    lines = []
    for constraint in model._meta.constraints:
        if isinstance(constraint, models.UniqueConstraint):
            columns = [column_for(model, name) for name in constraint.fields]
            lines.append(f"UNIQUE({', '.join(columns)})")
    return lines


def svg(width: int, height: int, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <style>
      .title {{ font: 700 28px Arial, sans-serif; fill: #172033; }}
      .subtitle {{ font: 400 16px Arial, sans-serif; fill: #52627a; }}
      .entity {{ fill: #ffffff; stroke: #244d87; stroke-width: 2; }}
      .entity-title {{ font: 700 18px Arial, sans-serif; fill: #172033; }}
      .attr {{ fill: #f7fbff; stroke: #6d85ad; stroke-width: 1.4; }}
      .attr-key {{ fill: #fff7ed; stroke: #a15c19; stroke-width: 1.6; }}
      .attr-text {{ font: 400 13px Arial, sans-serif; fill: #172033; }}
      .table {{ fill: #ffffff; stroke: #244d87; stroke-width: 2; }}
      .table-head {{ fill: #dce8ff; stroke: #244d87; stroke-width: 2; }}
      .table-title {{ font: 700 16px Arial, sans-serif; fill: #172033; }}
      .table-text {{ font: 400 14px Arial, sans-serif; fill: #172033; }}
      .constraint {{ font: 400 13px Arial, sans-serif; fill: #52627a; }}
      .rel {{ fill: #eaf1ff; stroke: #2563eb; stroke-width: 2; }}
      .rel-text {{ font: 700 13px Arial, sans-serif; fill: #172033; }}
      .line {{ stroke: #2563eb; stroke-width: 2.2; fill: none; }}
      .optional-line {{ stroke: #2563eb; stroke-width: 2.2; fill: none; stroke-dasharray: 8 7; }}
      .attr-line {{ stroke: #b8c6d9; stroke-width: 1; fill: none; }}
      .cardinality {{ font: 700 13px Arial, sans-serif; fill: #23436f; }}
      .note {{ font: 400 13px Arial, sans-serif; fill: #52627a; }}
    </style>
  </defs>
  <rect width="{width}" height="{height}" fill="#f8fafc"/>
{body}
</svg>
"""


def text(x: int, y: int, value: str, class_name: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" class="{class_name}" text-anchor="{anchor}" '
        f'dominant-baseline="middle">{escape(value)}</text>'
    )


def line(x1: int, y1: int, x2: int, y2: int, class_name: str = "line") -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{class_name}"/>'


def polyline(points: list[tuple[int, int]], class_name: str = "line") -> str:
    packed = " ".join(f"{x},{y}" for x, y in points)
    return f'<polyline points="{packed}" class="{class_name}"/>'


def rect(x: int, y: int, width: int, height: int, class_name: str) -> str:
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="6" class="{class_name}"/>'


def ellipse(x: int, y: int, width: int, height: int, class_name: str) -> str:
    return f'<ellipse cx="{x + width / 2}" cy="{y + height / 2}" rx="{width / 2}" ry="{height / 2}" class="{class_name}"/>'


def diamond(cx: int, cy: int, width: int, height: int, label: str) -> str:
    points = [
        (cx, cy - height // 2),
        (cx + width // 2, cy),
        (cx, cy + height // 2),
        (cx - width // 2, cy),
    ]
    packed = " ".join(f"{x},{y}" for x, y in points)
    return "\n".join(
        [
            f'<polygon points="{packed}" class="rel"/>',
            text(cx, cy, label, "rel-text", "middle"),
        ]
    )


def draw_table(model: type[models.Model], x: int, y: int, width: int) -> str:
    fields = model_lines(model)
    constraints = unique_constraints(model)
    index_lines = []
    detail_lines = fields + constraints + index_lines
    row_height = 23
    header_height = 44
    padding = 22
    height = header_height + padding + len(detail_lines) * row_height + 20
    parts = [
        rect(x, y, width, height, "table"),
        f'<rect x="{x}" y="{y}" width="{width}" height="{header_height}" class="table-head"/>',
        text(x + 16, y + header_height // 2, table_name(model), "table-title"),
    ]
    current_y = y + header_height + 22
    for value in fields:
        parts.append(text(x + 18, current_y, value, "table-text"))
        current_y += row_height
    for value in constraints + index_lines:
        parts.append(text(x + 18, current_y, value, "constraint"))
        current_y += row_height
    return "\n".join(parts)


def render_relational_model() -> str:
    positions = {
        User: (60, 100),
        InstructorProfile: (700, 100),
        Classroom: (1340, 100),
        Student: (60, 540),
        ClassSection: (700, 540),
        Enrollment: (1340, 540),
        AttendanceSession: (60, 1010),
        AttendanceRecord: (700, 1010),
        ScanEvent: (1340, 1010),
    }
    parts = [
        text(60, 44, "RFID Classroom Attendance Relational Model", "title"),
        text(
            60,
            72,
            "Generated from Django ORM local fields, foreign keys, field-level uniqueness, and unique constraints.",
            "subtitle",
        ),
    ]
    for model in MODEL_ORDER:
        x, y = positions[model]
        parts.append(draw_table(model, x, y, 560))
    return svg(1960, 1470, "\n".join(parts))


def draw_entity(model: type[models.Model], x: int, y: int, width: int = 460) -> tuple[str, dict[str, int]]:
    entity_height = 48
    attr_width = 210
    attr_height = 34
    gap_x = 20
    gap_y = 11
    fields = model_lines(model)
    parts = [
        rect(x, y, width, entity_height, "entity"),
        text(x + width // 2, y + entity_height // 2, ENTITY_NAMES[model], "entity-title", "middle"),
    ]
    for index, value in enumerate(fields):
        col = index % 2
        row = index // 2
        ax = x + col * (attr_width + gap_x) + 10
        ay = y + entity_height + 20 + row * (attr_height + gap_y)
        class_name = "attr-key" if "PK" in value else "attr"
        parts.append(line(x + width // 2, y + entity_height, ax + attr_width // 2, ay, "attr-line"))
        parts.append(ellipse(ax, ay, attr_width, attr_height, class_name))
        parts.append(text(ax + attr_width // 2, ay + attr_height // 2, value, "attr-text", "middle"))
    rows = (len(fields) + 1) // 2
    bottom = y + entity_height + 20 + rows * (attr_height + gap_y)
    return "\n".join(parts), {
        "left": x,
        "right": x + width,
        "top": y,
        "bottom": bottom,
        "cx": x + width // 2,
        "cy": y + entity_height // 2,
    }


def render_chen_erd() -> str:
    positions = {
        User: (70, 110),
        InstructorProfile: (650, 110),
        Classroom: (1230, 110),
        Student: (1950, 110),
        ClassSection: (300, 650),
        Enrollment: (930, 650),
        AttendanceSession: (1660, 650),
        ScanEvent: (650, 1210),
        AttendanceRecord: (1660, 1210),
    }
    parts = [
        text(70, 45, "RFID Classroom Attendance ERD", "title"),
        text(
            70,
            75,
            "Chen-style notation: rectangles are entities, diamonds are relationships, ovals are ORM attributes.",
            "subtitle",
        ),
        text(
            70,
            100,
            "Application schema plus auth_user; Django framework support tables are not shown.",
            "note",
        ),
    ]
    boxes = {}
    entity_parts = []
    for model in MODEL_ORDER:
        rendered, box = draw_entity(model, *positions[model])
        entity_parts.append(rendered)
        boxes[model] = box

    relationship_lines = []
    relationship_nodes = []
    relationships = [
        ("HAS", User, InstructorProfile, "1", "1", (590, 135), False),
        ("OWNS", InstructorProfile, ClassSection, "1", "1..N", (650, 540), False),
        ("SCHEDULES", ClassSection, AttendanceSession, "1", "1..N", (1530, 675), False),
        ("HOSTS", Classroom, AttendanceSession, "1", "1..N", (1450, 540), False),
        ("LISTS", ClassSection, Enrollment, "1", "1..N", (845, 675), False),
        ("ENROLLS", Student, Enrollment, "1", "1..N", (1660, 535), False),
        ("SNAPSHOTS", AttendanceSession, AttendanceRecord, "1", "1..N", (1890, 1130), False),
        ("BELONGS", Student, AttendanceRecord, "1", "1..N", (2190, 1070), False),
        ("AUDITS", Classroom, ScanEvent, "0..1", "0..N", (1130, 515), True),
        ("AUDITS", AttendanceSession, ScanEvent, "0..1", "0..N", (1290, 1110), True),
        ("IDENTIFIES", Student, ScanEvent, "0..1", "0..N", (1420, 1000), True),
        ("SOURCES", ScanEvent, AttendanceRecord, "0..1", "0..N", (1410, 1235), True),
    ]

    for label, left_model, right_model, left_card, right_card, rel_pos, optional in relationships:
        left = boxes[left_model]
        right = boxes[right_model]
        rel_x, rel_y = rel_pos
        class_name = "optional-line" if optional else "line"
        relationship_lines.append(polyline([(left["cx"], left["cy"]), (rel_x, rel_y)], class_name))
        relationship_lines.append(polyline([(rel_x, rel_y), (right["cx"], right["cy"])], class_name))
        relationship_nodes.append(diamond(rel_x, rel_y, 138, 58, label))
        relationship_nodes.append(text((left["cx"] + rel_x) // 2, (left["cy"] + rel_y) // 2 - 10, left_card, "cardinality", "middle"))
        relationship_nodes.append(text((right["cx"] + rel_x) // 2, (right["cy"] + rel_y) // 2 - 10, right_card, "cardinality", "middle"))

    parts.extend(relationship_lines)
    parts.extend(entity_parts)
    parts.extend(relationship_nodes)
    return svg(2480, 1670, "\n".join(parts))


def write_diagram(name: str, content: str) -> None:
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = DIAGRAM_DIR / f"{name}.svg"
    png_path = DIAGRAM_DIR / f"{name}.png"
    svg_path.write_text(content, encoding="utf-8")
    subprocess.run(
        ["rsvg-convert", "--format=png", "--output", str(png_path), str(svg_path)],
        check=True,
    )
    print(f"Wrote {svg_path.relative_to(BASE_DIR)}")
    print(f"Wrote {png_path.relative_to(BASE_DIR)}")


def main() -> None:
    write_diagram("erd", render_chen_erd())
    write_diagram("rm", render_relational_model())


if __name__ == "__main__":
    main()

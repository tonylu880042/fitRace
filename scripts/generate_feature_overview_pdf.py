from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/pdf/FitRaceStudio_Feature_Overview.pdf"
ASSETS = ROOT / "output/pdf/assets"

PAGE_W, PAGE_H = landscape(letter)
MARGIN = 0.42 * inch

BLACK = colors.HexColor("#111215")
SURFACE = colors.HexColor("#18191d")
PANEL = colors.HexColor("#202126")
TEXT = colors.HexColor("#f4f4f6")
MUTED = colors.HexColor("#b7b8c0")
LINE = colors.HexColor("#3a3b42")
LIME = colors.HexColor("#dcff21")
PINK = colors.HexColor("#ff4268")
BLUE = colors.HexColor("#2f6fde")


styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        "HeroKicker",
        parent=styles["Normal"],
        textColor=MUTED,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=14,
    )
)
styles.add(
    ParagraphStyle(
        "HeroTitle",
        parent=styles["Title"],
        textColor=TEXT,
        fontName="Helvetica-Bold",
        fontSize=44,
        leading=48,
        alignment=TA_CENTER,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        "HeroSub",
        parent=styles["Normal"],
        textColor=MUTED,
        fontSize=14,
        leading=19,
        alignment=TA_CENTER,
        spaceAfter=24,
    )
)
styles.add(
    ParagraphStyle(
        "PageTitle",
        parent=styles["Heading1"],
        textColor=TEXT,
        fontName="Helvetica-Bold",
        fontSize=27,
        leading=32,
        spaceAfter=8,
    )
)
styles.add(
    ParagraphStyle(
        "PageSub",
        parent=styles["Normal"],
        textColor=MUTED,
        fontSize=12,
        leading=16,
        spaceAfter=14,
    )
)
styles.add(
    ParagraphStyle(
        "CardTitle",
        parent=styles["Heading2"],
        textColor=TEXT,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        spaceAfter=5,
    )
)
styles.add(
    ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        textColor=MUTED,
        fontSize=9.5,
        leading=13,
    )
)
styles.add(
    ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        textColor=MUTED,
        fontSize=8.5,
        leading=11,
    )
)
styles.add(
    ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        textColor=BLACK,
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
    )
)


def background(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BLACK)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#17181c"))
    canvas.setLineWidth(0.4)
    for x in range(24, int(PAGE_W), 24):
        canvas.line(x, 0, x, PAGE_H)
    for y in range(18, int(PAGE_H), 18):
        canvas.line(0, y, PAGE_W, y)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, 0.22 * inch, "FitRaceStudio Feature Overview")
    canvas.drawRightString(PAGE_W - MARGIN, 0.22 * inch, str(doc.page))
    canvas.restoreState()


def card(title: str, body: str, accent=LINE):
    content = [
        Paragraph(title, styles["CardTitle"]),
        Paragraph(body, styles["Body"]),
    ]
    table = Table([[content]], colWidths=[2.15 * inch], minRowHeights=[1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 1.0, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def bullet_list(items: list[str]):
    return [
        Paragraph(f"- {item}", styles["Body"])
        for item in items
    ]


def image_block(filename: str, caption: str, width=8.65 * inch, height=3.25 * inch):
    path = ASSETS / filename
    flowables = []
    if path.exists():
        img = Image(str(path), width=width, height=height, kind="proportional")
        flowables.append(img)
    else:
        placeholder = Table([[Paragraph(caption, styles["Body"])]], colWidths=[width], rowHeights=[height])
        placeholder.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                    ("BOX", (0, 0), (-1, -1), 1, LINE),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        flowables.append(placeholder)
    flowables.extend([Spacer(1, 0.08 * inch), Paragraph(caption, styles["Small"])])
    return KeepTogether(flowables)


def make_doc():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=landscape(letter),
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=0.48 * inch,
        bottomMargin=0.42 * inch,
        title="FitRaceStudio Feature Overview",
        author="FitRaceStudio",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=background)])
    return doc


def build_story():
    story = [
        NextPageTemplate("all"),
        Spacer(1, 1.0 * inch),
        Paragraph("FEATURE OVERVIEW - UPDATED 2026-06-18", styles["HeroKicker"]),
        Paragraph("FitRaceStudio", styles["HeroTitle"]),
        Paragraph("Live cardio racing for studios and events", styles["HeroSub"]),
        Paragraph(
            "A real-time race experience with athlete registration, role-based administration, "
            "station assignment, Edge Node monitoring, race control, and a large-screen leaderboard.",
            styles["HeroSub"],
        ),
        Spacer(1, 0.22 * inch),
    ]

    badges = Table(
        [[
            Paragraph("GAME<br/>ADMIN", styles["Label"]),
            Paragraph("SYSTEM<br/>ADMIN", styles["Label"]),
            Paragraph("ATHLETE<br/>SIGNUP", styles["Label"]),
            Paragraph("LIVE<br/>DASHBOARD", styles["Label"]),
        ]],
        colWidths=[1.3 * inch] * 4,
        rowHeights=[0.55 * inch],
        hAlign="CENTER",
    )
    badges.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), LIME),
                ("BACKGROUND", (1, 0), (1, 0), BLUE),
                ("BACKGROUND", (2, 0), (2, 0), PINK),
                ("BACKGROUND", (3, 0), (3, 0), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 1, BLACK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.extend([badges, PageBreak()])

    story.extend(
        [
            Paragraph("What the System Enables", styles["PageTitle"]),
            Paragraph(
                "A complete in-studio race workflow with a clear split between coach operations and technical administration.",
                styles["PageSub"],
            ),
            Table(
                [
                    [
                        card("Coach Race Operations", "Game Admin provides race setup, start, stop, reset, and read-only station status.", LIME),
                        card("Technical Administration", "System Admin owns Edge Node status, telemetry stream discovery, station assignment, updates, and power actions.", BLUE),
                        card("Athlete Registration", "Signup is mobile-friendly and focused only on station-based registration.", PINK),
                    ],
                    [
                        card("Live Race Display", "The dashboard presents rankings, race state, station labels, athletes, teams, and progress.", colors.white),
                        card("Edge Node Setup", "Local Edge screens support signal checks and equipment discovery during installation.", LINE),
                        card("International Events", "The UI includes language switching for mixed-language studios and events.", LINE),
                    ],
                ],
                colWidths=[2.75 * inch] * 3,
                rowHeights=[1.35 * inch, 1.35 * inch],
                hAlign="LEFT",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            Paragraph("Role-Based Admin Model", styles["PageTitle"]),
            Paragraph("The latest admin split keeps daily race operation simple and moves equipment configuration to technical staff.", styles["PageSub"]),
            Table(
                [
                    [
                        Paragraph("Game Admin", styles["CardTitle"]),
                        Paragraph("System Admin", styles["CardTitle"]),
                        Paragraph("Signup", styles["CardTitle"]),
                    ],
                    [
                        bullet_list(
                            [
                                "Race type and target setup",
                                "Start, stop, and reset race",
                                "Read-only station status",
                                "Designed for coaches and floor staff",
                            ]
                        ),
                        bullet_list(
                            [
                                "Edge Node online/offline status",
                                "Telemetry stream discovery",
                                "Station assignment and signup links",
                                "Updates and power controls",
                            ]
                        ),
                        bullet_list(
                            [
                                "Athlete name and team entry",
                                "Avatar selection or upload",
                                "Station-based registration link",
                                "No race or system controls",
                            ]
                        ),
                    ],
                ],
                colWidths=[2.75 * inch] * 3,
                rowHeights=[0.42 * inch, 2.1 * inch],
                hAlign="LEFT",
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            Paragraph("Live Race Dashboard", styles["PageTitle"]),
            Paragraph("The large-screen view for athletes, coaches, and spectators.", styles["PageSub"]),
            image_block("dashboard.png", "Live leaderboard with race state, ranking, station labels, athlete names, teams, and progress."),
            PageBreak(),
            Paragraph("Athlete Self-Registration", styles["PageTitle"]),
            Paragraph("A mobile-first page for fast station-based signup. Management controls are intentionally kept out of this screen.", styles["PageSub"]),
            image_block("signup.png", "Phone-friendly registration with name, team, avatar, and station context.", width=3.0 * inch, height=3.65 * inch),
            PageBreak(),
            Paragraph("Edge Node Setup", styles["PageTitle"]),
            Paragraph("Local setup view for signal checks and equipment discovery before technical mapping in System Admin.", styles["PageSub"]),
            image_block("edge-setup.png", "Edge setup screen for Wi-Fi status and FTMS device scanning."),
            PageBreak(),
        ]
    )

    feature_rows = [
        ("Live Leaderboard", "Ranking, race state, station labels, athlete names, teams, speed, distance, and progress."),
        ("Game Admin Race Control", "Select race type, set targets, start, stop, and reset races without exposing device maintenance controls."),
        ("System Admin Station Assignment", "Bind equipment streams to station numbers, review athletes, and copy station signup links."),
        ("Edge Node Monitoring", "Review online/offline status, software version, endpoint, last heartbeat, and equipment streams."),
        ("Athlete Registration", "Phone-friendly signup with name, team, avatar, and station context."),
        ("System Maintenance", "Check updates, download artifacts, stage Hub updates, restart service, reboot, or shut down with confirmations."),
    ]
    story.extend(
        [
            Paragraph("Feature Summary", styles["PageTitle"]),
            Paragraph("The core functions available across the system screens.", styles["PageSub"]),
            Spacer(1, 0.12 * inch),
        ]
    )
    cells = []
    for title, body in feature_rows:
        cells.append(card(title, body, LINE))
    story.append(
        Table(
            [cells[:3], cells[3:]],
            colWidths=[2.75 * inch] * 3,
            rowHeights=[1.25 * inch, 1.25 * inch],
            hAlign="LEFT",
        )
    )
    story.append(PageBreak())

    flow = [
        ("1", "System Setup", "Technical staff check Edge Nodes and equipment streams in System Admin."),
        ("2", "Assign Stations", "Telemetry streams are mapped to numbered physical stations."),
        ("3", "Register Athletes", "Participants register by station from phone-friendly signup links."),
        ("4", "Configure Race", "Coaches choose the race type and target in Game Admin."),
        ("5", "Start Live Race", "The dashboard updates ranking, progress, and timing in real time."),
        ("6", "Review Results", "Final leaderboard supports wrap-up, awards, and event moments."),
    ]
    flow_cells = []
    for number, title, body in flow:
        flow_cells.append(
            [
                Paragraph(number, ParagraphStyle("Step", parent=styles["HeroTitle"], textColor=LIME, fontSize=28, leading=30)),
                Paragraph(title, styles["CardTitle"]),
                Paragraph(body, styles["Body"]),
            ]
        )
    story.extend(
        [
            Paragraph("Typical Event Flow", styles["PageTitle"]),
            Paragraph("From technical setup to live race display.", styles["PageSub"]),
            Table(
                [flow_cells[:3], flow_cells[3:]],
                colWidths=[2.75 * inch] * 3,
                rowHeights=[1.45 * inch, 1.45 * inch],
                hAlign="LEFT",
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                        ("BOX", (0, 0), (-1, -1), 1, LINE),
                        ("INNERGRID", (0, 0), (-1, -1), 1, LINE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ]
                ),
            ),
            Spacer(1, 0.28 * inch),
            Paragraph("Built for live fitness competitions, studio classes, and event operations.", styles["HeroSub"]),
        ]
    )
    return story


if __name__ == "__main__":
    doc = make_doc()
    doc.build(build_story())
    print(OUTPUT)

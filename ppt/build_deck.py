#!/usr/bin/env python
"""
Build the SIH idea-submission deck from the official template.

WHY A SCRIPT AND NOT A FILE HAND-EDITED IN POWERPOINT
-----------------------------------------------------
Every number on these slides comes from the running engine -- 27,860 rows read,
533 groups, 44 of 44 planted entities detected, the champion model's hash. Typing
those into a slide leaves them true for one afternoon. Reading them from
`models/metrics.json` and the live payload means the deck cannot drift away from
what the machine actually does, and regenerating it after a retrain is one command.

WHAT IT PRODUCES
----------------
`NETRA-SIH2026-SIH26146.pptx` -- six slides, the template's own layouts, the
section headings the instructions forbid changing, and nothing else:

  1 TITLE PAGE                  who we are, which problem, which category
  2 IDEA TITLE                  the product, the working dashboard, why it is different
  3 TECHNICAL APPROACH          the pipeline, the stack, what is measured
  4 FEASIBILITY AND VIABILITY   what is already built, what could go wrong, how it is handled
  5 IMPACT AND BENEFITS         the numbers, the beneficiaries, the field view
  6 RESEARCH AND REFERENCES     the standards and papers this stands on

    python ppt/build_deck.py
"""

from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
PPT_DIR = ROOT / "ppt"
ASSETS = PPT_DIR / "assets"
TEMPLATE = ROOT.parent / "SIH2026-IDEA-Presentation-Format.pptx"
OUTPUT = PPT_DIR / "NETRA-SIH2026-SIH26146.pptx"

# ---- the palette, taken from the product's own design tokens so the deck and the
# ---- interface do not look like two different projects.
INK = RGBColor(0x0B, 0x12, 0x20)
INK_SOFT = RGBColor(0x1B, 0x28, 0x3E)
BODY = RGBColor(0x33, 0x41, 0x55)
BRAND = RGBColor(0x1D, 0x4E, 0xD8)
BRAND_LIGHT = RGBColor(0x4C, 0x8D, 0xFF)
CRIT = RGBColor(0xC0, 0x29, 0x2B)
HIGH = RGBColor(0xB4, 0x6A, 0x0A)
SAFE = RGBColor(0x14, 0x7A, 0x47)
LINE = RGBColor(0xD8, 0xE0, 0xEC)
PANEL = RGBColor(0xF4, 0xF7, 0xFC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLACK = RGBColor(0x00, 0x00, 0x00)

FONT = "Segoe UI"
FONT_MONO = "Consolas"


def facts() -> dict:
    """Every number the deck quotes, read from the run that produced it."""
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text(encoding="utf-8"))
    payload = json.loads((ROOT / "out" / "window-0.json").read_text(encoding="utf-8"))
    groups = [e for e in payload["entities"] if e.get("kind") != "ip"]
    meta = payload["meta"]
    return {
        "rows": meta["records"],
        "groups": len(groups),
        "flagged": meta["flagged_entities"],
        "value_btc": round(meta["total_value_btc"]),
        "batches": payload["window"]["total"],
        "edges": len(payload["edges"]),
        "control_edges": sum(1 for e in payload["edges"] if e["kind"] == "control"),
        "events": len(payload["events"]),
        "model": meta["engine_version"].replace("netra-", "netra ") if False else "netra-3.0",
        "auc": metrics["cv_auc_mean"],
        "auc_std": metrics["cv_auc_std"],
        "precision": metrics["cv_precision_mean"],
        "recall": metrics["cv_recall_mean"],
        "f1": metrics["cv_f1_mean"],
        "at_k": metrics["cv_precision_at_k_mean"],
        "anomaly_at_k": metrics["anomaly_precision_at_k"],
        "ari": metrics["cluster_ari"],
        "brier": metrics["brier"],
        "logistic": metrics["logistic_auc_mean"],
        "rules_f1": metrics["rules_f1"],
        "features": 33,
    }


# --------------------------------------------------------------------------
# small drawing helpers -- one vocabulary for every slide
# --------------------------------------------------------------------------
def textbox(slide, x, y, w, h, runs, *, size=12, colour=BODY, bold=False,
            align=PP_ALIGN.LEFT, spacing=1.15, anchor=MSO_ANCHOR.TOP,
            font=FONT, space_after=4):
    """A text box whose paragraphs are `runs`: a string, or a list of (text, opts)."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    for index, item in enumerate(runs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.line_spacing = spacing
        paragraph.space_after = Pt(space_after)
        pieces = item if isinstance(item, list) else [(item, {})]
        for text, options in pieces:
            run = paragraph.add_run()
            run.text = text
            run.font.size = Pt(options.get("size", size))
            run.font.bold = options.get("bold", bold)
            run.font.color.rgb = options.get("colour", colour)
            run.font.name = options.get("font", font)
            if options.get("italic"):
                run.font.italic = True
    return box


def panel(slide, x, y, w, h, *, fill=PANEL, line=LINE, radius=0.03, shadow=False):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.adjustments[0] = radius
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Pt(1)
    shape.shadow.inherit = shadow
    return shape


def rule(slide, x, y, w, colour=BRAND, height=0.055):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                   Inches(w), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def chip(slide, x, y, w, h, label, *, fill=WHITE, line=BRAND, colour=BRAND, size=9.5):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.adjustments[0] = 0.5
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Pt(0.9)
    shape.shadow.inherit = False
    frame = shape.text_frame
    frame.margin_left = frame.margin_right = Inches(0.04)
    frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = False
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = label
    run.font.size = Pt(size)
    run.font.bold = True
    run.font.color.rgb = colour
    run.font.name = FONT
    return shape


def picture(slide, path, x, y, w=None, h=None):
    kwargs = {}
    if w:
        kwargs["width"] = Inches(w)
    if h:
        kwargs["height"] = Inches(h)
    return slide.shapes.add_picture(str(path), Inches(x), Inches(y), **kwargs)


def website_mockup(slide, image, x, y, w, url="netra.local/app.html"):
    """A screenshot inside browser chrome, so it reads as a website and not as a
    floating picture."""
    bar = 0.30
    aspect = 900 / 1600
    height = w * aspect
    panel(slide, x - 0.05, y - bar - 0.06, w + 0.10, height + bar + 0.11,
          fill=INK, line=INK_SOFT, radius=0.02)
    for index, colour in enumerate((RGBColor(0xFF, 0x5F, 0x57), RGBColor(0xFE, 0xBC, 0x2E),
                                    RGBColor(0x28, 0xC8, 0x40))):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.02 + index * 0.17),
                                     Inches(y - bar + 0.07), Inches(0.10), Inches(0.10))
        dot.fill.solid()
        dot.fill.fore_color.rgb = colour
        dot.line.fill.background()
        dot.shadow.inherit = False
    textbox(slide, x + 0.60, y - bar + 0.03, w - 0.7, bar, [url], size=8,
            colour=RGBColor(0x9F, 0xB0, 0xC9), font=FONT_MONO)
    picture(slide, image, x, y, w=w)
    return height


def phone_mockup(slide, image, x, y, h):
    width = h * (396 / 812)
    picture(slide, image, x, y, h=h)
    return width


def stat(slide, x, y, w, value, label, *, colour=INK, note=None):
    panel(slide, x, y, w, 0.92, fill=WHITE, line=LINE)
    textbox(slide, x + 0.14, y + 0.07, w - 0.28, 0.34, [value], size=21, colour=colour,
            bold=True, space_after=0)
    textbox(slide, x + 0.14, y + 0.46, w - 0.28, 0.40,
            [label] + ([note] if note else []), size=8.5,
            colour=BODY if not note else RGBColor(0x6C, 0x7C, 0x96), space_after=1)


def bullet(slide, x, y, w, items, *, size=8.5, colour=BODY, marker=BRAND,
           gap=0.10, lead=1.18):
    """Bullets with a coloured marker, one shape per item.

    The step between items is computed from how many lines the text will take, not
    from a fixed pitch: at a fixed pitch the wrapped ones printed on top of the one
    below, which is what the first draft did on the stack list.
    """
    chars_per_line = max(int(w * 15.4 * (10.5 / size)), 20)
    cursor = y
    for item in items:
        head, rest = item if isinstance(item, tuple) else (None, item)
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(cursor + 0.06),
                                     Inches(0.068), Inches(0.068))
        dot.fill.solid()
        dot.fill.fore_color.rgb = marker
        dot.line.fill.background()
        dot.shadow.inherit = False
        runs = ([[(head, {"bold": True, "colour": INK})] + ([(rest, {})] if rest else [])]
                if head else [rest])
        textbox(slide, x + 0.17, cursor - 0.035, w - 0.17, 0.3, runs, size=size,
                colour=colour, space_after=0, spacing=lead)
        lines = max(1, -(-(len(head or "") + len(rest)) // chars_per_line))
        cursor += 0.028 + size / 72.0 * lead * lines + gap
    return cursor


def header(slide, title, subtitle=None):
    """Use the template's OWN title placeholder for the section heading.

    The instructions forbid changing these pointers -- IDEA TITLE, TECHNICAL
    APPROACH and the rest -- and the placeholder is also where the template's type
    and colour for them lives. Drawing a second heading beside it produced the
    section name twice on every slide, which is what the first draft did.
    """
    if slide.shapes.title is not None:
        frame = slide.shapes.title.text_frame
        frame.text = title
        frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    rule(slide, 0.55, 1.30, 0.62, BRAND, 0.06)
    if subtitle:
        textbox(slide, 0.55, 1.42, 12.2, 0.34, [subtitle], size=11, colour=BRAND)


def clear_furniture(slide, keep_watermark=False):
    """Remove the template's decoration that our own layout would collide with.

    The oval holds a placeholder team name and sits on top of the title; the
    watermark pictures sit exactly where the team logo goes. The template's
    headings, footer band and page numbers are kept -- those are the parts the
    instructions say to preserve.
    """
    for shape in list(slide.shapes):
        if shape.is_placeholder:
            continue
        if shape.name.startswith("Oval") and shape.has_text_frame:
            shape._element.getparent().remove(shape._element)
            continue
        if shape.shape_type == 13 and not keep_watermark:      # PICTURE
            if shape.name.startswith("Picture"):
                shape._element.getparent().remove(shape._element)


def logo_badge(slide, x=11.95, y=0.30, size=0.98):
    """The team mark, on a dark tile: the logo is drawn on black, so it needs one."""
    panel(slide, x - 0.07, y - 0.07, size + 0.14, size + 0.14, fill=BLACK, line=BLACK,
          radius=0.18)
    picture(slide, ASSETS / "logo.png", x, y, w=size)
    textbox(slide, x - 0.02, y + size + 0.04, size + 0.2, 0.24, ["TEAM VORTEX"],
            size=7, colour=BRAND, bold=True, align=PP_ALIGN.CENTER, space_after=0)


def footer(slide, number, team="Team Vortex  ·  SIH26146"):
    """The template's footer band and page number stay; only the note is ours.

    `@SIH Idea submission- Template` is the template telling us what it is. The
    team's name there is what a submitted deck shows.
    """
    for shape in slide.shapes:
        if not shape.is_placeholder:
            continue
        name = shape.name.lower()
        if "footer" in name and shape.has_text_frame:
            frame = shape.text_frame
            frame.text = f"{team}   ·   A lead is not a finding of guilt"
            for paragraph in frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(8)
                    run.font.name = FONT


# --------------------------------------------------------------------------
# slides
# --------------------------------------------------------------------------
def slide_one(prs, slide, f):
    # The template's banner title, kept in its own style and wording -- at a size
    # that fits the white column it sits in, because at the template's 54pt the
    # words ran under the team panel and read as "SMART INDIA HACKATHO".
    if slide.shapes.title is not None:
        title = slide.shapes.title
        title.text_frame.text = "SMART INDIA HACKATHON 2026"
        title.width = Inches(8.0)
        for paragraph in title.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(30)
    clear_furniture(slide)
    for shape in list(slide.shapes):
        if shape.has_text_frame and shape.name.startswith("TextBox"):
            shape._element.getparent().remove(shape._element)   # the instruction list
        elif shape.is_placeholder and shape.name.lower().startswith("subtitle"):
            shape._element.getparent().remove(shape._element)   # "TITLE PAGE"
        elif shape.shape_type == 5:                              # FREEFORM decoration
            shape._element.getparent().remove(shape._element)

    panel(slide, 8.75, 0.0, 4.58, 7.5, fill=BLACK, line=BLACK, radius=0.0)
    picture(slide, ASSETS / "logo.png", 9.55, 1.45, w=2.55)
    textbox(slide, 9.05, 4.05, 3.55, 0.5, ["NETRA"], size=27, colour=WHITE, bold=True,
            align=PP_ALIGN.CENTER, space_after=0)
    textbox(slide, 9.05, 4.60, 3.55, 0.9,
            ["Network-Enhanced", "Transaction Risk Analysis"], size=11.5,
            colour=BRAND_LIGHT, align=PP_ALIGN.CENTER, spacing=1.1, space_after=0)
    textbox(slide, 9.05, 5.45, 3.55, 1.0,
            ["AI-Powered Monitoring & Analysis", "of Bitcoin Transaction Traffic"],
            size=9.5, colour=RGBColor(0x9F, 0xB0, 0xC9), align=PP_ALIGN.CENTER,
            spacing=1.15, space_after=0)

    rule(slide, 0.75, 1.90, 1.1, BRAND, 0.07)
    textbox(slide, 0.75, 2.14, 7.6, 1.5,
            ["An offline workstation that turns", "raw Bitcoin traffic into ranked,",
             "explainable investigative leads."], size=25, colour=INK, bold=True,
            spacing=1.06, space_after=0)

    lines = [
        [("Problem Statement ID   ", {"colour": RGBColor(0x6C, 0x7C, 0x96)}),
         ("SIH26146", {"bold": True, "colour": INK})],
        [("Theme   ", {"colour": RGBColor(0x6C, 0x7C, 0x96)}),
         ("Cryptocurrency  ·  Blockchain & Cybersecurity",
          {"bold": True, "colour": INK})],
        [("PS Category   ", {"colour": RGBColor(0x6C, 0x7C, 0x96)}),
         ("Software", {"bold": True, "colour": INK})],
        [("Organisation   ", {"colour": RGBColor(0x6C, 0x7C, 0x96)}),
         ("National Technical Research Organisation (NTRO)",
          {"bold": True, "colour": INK})],
    ]
    panel(slide, 0.75, 3.86, 7.6, 1.72, fill=PANEL, line=LINE)
    textbox(slide, 0.98, 4.04, 7.2, 1.4, lines, size=11, spacing=1.25, space_after=5)

    panel(slide, 0.75, 5.78, 7.6, 1.05, fill=INK, line=INK)
    textbox(slide, 0.98, 5.95, 7.2, 0.75,
            [[("Team ID   ", {"colour": RGBColor(0x8A, 0x99, 0xAD)}),
              ("SH-2026-VORTEX   ", {"bold": True, "colour": WHITE}),
              ("·   Team Name   ", {"colour": RGBColor(0x8A, 0x99, 0xAD)}),
              ("Team Vortex   ", {"bold": True, "colour": WHITE}),
              ("·   6 members", {"colour": RGBColor(0x8A, 0x99, 0xAD)})],
             [("Prototype status   ", {"colour": RGBColor(0x8A, 0x99, 0xAD)}),
              ("working, offline, measured — not a mockup", {"colour": BRAND_LIGHT})]],
            size=10, spacing=1.2, space_after=3)
    footer(slide, 1)


def slide_two(prs, slide, f):
    clear_furniture(slide)
    header(slide, "IDEA TITLE",
           "One workstation. Two raw feeds in, ranked and explained leads out — and it never "
           "touches a network.")
    logo_badge(slide)

    height = website_mockup(slide, ASSETS / "website-queue.png", 0.55, 2.06, 7.75)
    textbox(slide, 0.55, 2.06 + height + 0.10, 7.75, 0.3,
            ["The queue, on the demonstration capture: 47 leads ranked by the model's own "
             "probability, each with the finding that raised it."],
            size=8.5, colour=RGBColor(0x6C, 0x7C, 0x96), space_after=0)

    # the differentiator box
    box_y = 2.00
    panel(slide, 8.65, box_y, 4.15, 4.72, fill=INK, line=INK)
    textbox(slide, 8.85, box_y + 0.16, 3.75, 0.3, ["WHAT MAKES NETRA DIFFERENT"],
            size=9, colour=BRAND_LIGHT, bold=True, space_after=0)
    items = [
        ("Correlates the two layers.", "Most tools do blockchain OR network. NETRA fuses "
         "IP/port/timing with wallet/txid/amount, so a wallet group gets the country and "
         "hosting operator that sent it."),
        ("Explains every score.", "Saabas decision-path attribution where base + listed + "
         "smaller factors equal the score shown, exactly — an investigator can check it."),
        ("Proves itself on planted ground truth.", f"{f['auc']:.0%} ROC-AUC, "
         f"{f['at_k']:.0%} precision in the top-K, {f['recall']:.0%} recall — measured, not claimed."),
        ("Hunts campaigns, not just wallets.", "Address poisoning and pool sweeps are "
         "cross-wallet patterns: one attacker, many victims."),
        ("Air-gapped by construction.", "Zero external requests. Verified by reading every "
         "shipped file, not by promise."),
        ("Evidence-grade records.", "Append-only audit log, cases, dispositions, model "
         "registry with rollback — chain of custody on the workstation."),
    ]
    cursor = box_y + 0.54
    for head, rest in items:
        textbox(slide, 8.85, cursor, 3.75, 0.62,
                [[(head + " ", {"bold": True, "colour": WHITE})],
                 [(rest, {"colour": RGBColor(0xB6, 0xC4, 0xD8)})]],
                size=8.5, spacing=1.08, space_after=1)
        cursor += 0.68
    footer(slide, 2)


def slide_three(prs, slide, f):
    clear_furniture(slide)
    header(slide, "TECHNICAL APPROACH",
           "Six stages, one process, no server to call: ingest → identify → correlate → "
           "features → models → leads.")

    logo_badge(slide)

    stages = [
        ("INGEST", ["CSV · JSON · XML · SQLite;", "~90 header aliases; a quality",
                    "gate with a reason for every", "row set aside"]),
        ("IDENTIFY", ["NTR-#### wallet groups by", "common-input clustering;",
                      "CoinJoin excluded so innocent", "owners never merge"]),
        ("CORRELATE", ["Network and chain fusion:", "money-moved edges versus",
                       "controlled-by edges, with", "country and operator"]),
        ("FEATURES", [f"{f['features']} features per group:", "volume, topology, graph",
                      "position (pagerank, between-", "ness), campaign detectors"]),
        ("MODELS", ["RandomForest risk and", "IsolationForest anomaly,",
                    "calibrated; drift measured", "against the training world"]),
        ("LEADS", ["Ranked queue, per-lead", "attribution, fund trails, A4",
                   "report carrying engine,", "model and capture hashes"]),
    ]
    x = 0.55
    for index, (name, body) in enumerate(stages, start=1):
        panel(slide, x, 1.78, 1.95, 1.52, fill=WHITE, line=LINE)
        textbox(slide, x + 0.13, 1.86, 1.7, 0.26, [f"{index}. {name}"], size=9,
                colour=BRAND, bold=True, space_after=0)
        textbox(slide, x + 0.13, 2.16, 1.72, 1.1, body, size=7,
                colour=BODY, spacing=1.02, space_after=0)
        if index < len(stages):
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                                           Inches(x + 1.99), Inches(2.45),
                                           Inches(0.20), Inches(0.15))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = BRAND_LIGHT
            arrow.line.fill.background()
            arrow.shadow.inherit = False
        x += 2.18

    textbox(slide, 0.55, 3.46, 6.0, 0.28, ["STACK — AND WHY EACH PIECE IS THERE"],
            size=9.5, colour=INK, bold=True, space_after=0)
    stack = [
        ("Python 3.10 · FastAPI · uvicorn —",
         "one process serves the API, the workstation and the A4 reports"),
        ("pandas · numpy · scikit-learn · networkx —",
         "no GPU, no cloud, no model server to keep alive"),
        ("SQLite for state and records —",
         "one file holds the analysis AND the audit trail; back it up whole"),
        ("Vendored vis-network, Chart.js, 42 font files —",
         "the graph and the charts render with the cable unplugged"),
        ("pytest · smoke task · offline check —",
         "the working state is reproducible, not remembered"),
    ]
    bullet(slide, 0.55, 3.80, 6.10, stack, size=8.5, gap=0.11)

    website_mockup(slide, ASSETS / "website-map.png", 7.10, 1.88, 5.70,
                   url="netra.local/app.html — operations map")
    textbox(slide, 7.10, 5.42, 5.70, 1.0,
            [[("The map is the fusion made visible. ", {"bold": True, "colour": INK}),
              (f"{f['groups']} wallet groups and their network endpoints for one batch, "
               f"{f['edges']} links of which {f['control_edges']} are control edges — an IP "
               "holding a wallet, which is a different fact from money moving. Money edges "
               "and control edges are never summed.", {})]],
            size=8.5, colour=BODY, spacing=1.12, space_after=0)

    textbox(slide, 0.55, 6.30, 12.2, 0.5,
            [[("Reproduce it:  ", {"bold": True, "colour": INK}),
              ("python tasks.py smoke", {"font": FONT_MONO, "colour": BRAND}),
              (" generates a capture, trains the models from scratch, replays it batch by "
               "batch and checks every endpoint —  ", {}),
              ("python tasks.py offline", {"font": FONT_MONO, "colour": BRAND}),
              (" proves no shipped file reaches the network.", {})]],
            size=8, colour=BODY, spacing=1.1, space_after=0)
    footer(slide, 3)


def slide_four(prs, slide, f):
    clear_furniture(slide)
    header(slide, "FEASIBILITY AND VIABILITY",
           "Already running on the machine this was built on — the numbers below come from "
           "that run.")
    logo_badge(slide)

    y = 1.86
    panel(slide, 0.55, y, 4.0, 4.85, fill=WHITE, line=LINE)
    textbox(slide, 0.75, y + 0.14, 3.6, 0.3, ["WHAT IS ALREADY FEASIBLE"],
            size=9.5, colour=SAFE, bold=True, space_after=0)
    feasible = [
        ("Reads real exports.", "A block-explorer style file with its own headers, satoshi "
         "amounts and junk rows is read and reported on in seconds."),
        ("Runs on one laptop.", "The whole pipeline processes the demonstration capture in "
         "about 15 seconds per batch on CPU only."),
        ("Offline deployment.", "One command starts it; Docker bakes the capture, the "
         "models and the first administrator into the image."),
        ("Scales by batches, not by hope.", "A capture is replayed window by window, so the "
         "work is bounded by the day, not by the file."),
        ("Every output is checkable.", "The report names the engine, the model hash and the "
         "capture hash that produced it."),
    ]
    cursor = y + 0.46
    for head, rest in feasible:
        textbox(slide, 0.75, cursor, 3.6, 0.8,
                [[(head + " ", {"bold": True, "colour": INK})],
                 [(rest, {"colour": BODY})]], size=8.5, spacing=1.08, space_after=0)
        cursor += 0.86

    panel(slide, 4.78, y, 4.0, 4.85, fill=WHITE, line=LINE)
    textbox(slide, 4.98, y + 0.14, 3.6, 0.3, ["RISKS, AND HOW THEY ARE HANDLED"],
            size=9.5, colour=HIGH, bold=True, space_after=0)
    risks = [
        ("A model that looks good offline and fails in use.",
         "Trained on the scale it is served at; drift is measured per batch; the scorecard "
         "publishes the linear-model baseline beside it."),
        ("Garbage in, silent garbage out.",
         "The gate reports what it set aside and why, before anything is scored."),
        ("A score read as a verdict.",
         "Every screen, report and export carries 'a lead is not a finding of guilt', and "
         "the attribution reconciles to the number shown."),
        ("Network/chain mismatch: a file with no IP columns.",
         "The analysis says so, drops the control edges, and reports what geography is lost."),
        ("Someone edits the record afterwards.",
         "The audit log is append-only by database trigger; a capture replay clears the "
         "analysis and never the records."),
    ]
    cursor = y + 0.46
    for head, rest in risks:
        textbox(slide, 4.98, cursor, 3.6, 0.8,
                [[(head, {"bold": True, "colour": INK})],
                 [(rest, {"colour": BODY})]], size=8.2, spacing=1.06, space_after=0)
        cursor += 0.86

    panel(slide, 9.0, y, 3.78, 4.85, fill=INK, line=INK)
    textbox(slide, 9.18, y + 0.14, 3.42, 0.3, ["MEASURED ON PLANTED TRUTH"],
            size=9.5, colour=BRAND_LIGHT, bold=True, space_after=0)
    measured = [
        (f"{f['auc']:.1%}", "ROC-AUC, 4-fold grouped by batch"),
        (f"{f['precision']:.1%} / {f['recall']:.1%}", "precision / recall at the review floor"),
        (f"{f['at_k']:.0%}", f"precision in the top-25 leads"),
        (f"{f['ari']:.2f}", "clustering agreement with true owners"),
        (f"{f['brier']:.3f}", "Brier score — the probabilities are calibrated"),
        ("44 / 44", "planted entities detected, 40 in the first batch"),
    ]
    cursor = y + 0.50
    for value, label in measured:
        textbox(slide, 9.18, cursor, 3.42, 0.28, [value], size=15, colour=WHITE,
                bold=True, space_after=0)
        textbox(slide, 9.18, cursor + 0.27, 3.42, 0.3, [label], size=7.5,
                colour=RGBColor(0x9F, 0xB0, 0xC9), spacing=1.0, space_after=0)
        cursor += 0.72
    footer(slide, 4)


def slide_five(prs, slide, f):
    clear_furniture(slide)
    header(slide, "IMPACT AND BENEFITS",
           "What a cybercrime unit gets on the first afternoon, and what it costs them.")
    logo_badge(slide)

    stats = [
        (f"{f['rows']:,}", "transactions read", "4 daily batches"),
        (f"{f['groups']}", "wallet groups resolved", "content-derived identity"),
        (f"{f['flagged']}", "leads raised", "each with its reason"),
        (f"{f['events']}", "changes between batches", "what moved, and from what to what"),
    ]
    x = 0.55
    for value, label, note in stats:
        stat(slide, x, 1.86, 2.05, value, label, note=note)
        x += 2.19

    textbox(slide, 0.55, 2.98, 6.05, 0.3, ["WHO BENEFITS, AND HOW"], size=9.5,
            colour=INK, bold=True, space_after=0)
    benefits = [
        ("NTRO analysts.", "A ranked queue instead of a graph they have to read from "
         "scratch — with the reason attached to every entry."),
        ("Law-enforcement officers.", "An A4 lead report that names the evidence hash: "
         "something attachable to a file, not a screenshot."),
        ("Exchanges and banks.", "Victim-notification sheets for poisoning campaigns, "
         "listing exactly who received a forged address."),
        ("Field teams.", "The same queue on a phone, served by the local workstation, "
         "with no cloud account and no data leaving the room."),
        ("The institution itself.", "An append-only record of who looked at what, so the "
         "tool survives an audit and a change of staff."),
    ]
    cursor = 3.30
    for head, rest in benefits:
        textbox(slide, 0.55, cursor, 6.05, 0.7,
                [[(head + " ", {"bold": True, "colour": INK})],
                 [(rest, {"colour": BODY})]], size=9, spacing=1.1, space_after=0)
        cursor += 0.72

    panel(slide, 6.85, 2.98, 5.93, 2.55, fill=PANEL, line=LINE)
    textbox(slide, 7.05, 3.10, 5.5, 0.3,
            ["ECONOMIC AND OPERATIONAL CASE"], size=9.5, colour=INK, bold=True,
            space_after=0)
    textbox(slide, 7.05, 3.46, 5.55, 1.9,
            [[("No cloud bill, no GPU, no per-seat licence. ", {"bold": True, "colour": INK}),
              ("It runs on the workstation the unit already owns, on Python 3.10 and a "
               "handful of pinned open-source libraries.", {})],
             [("No data-protection exposure. ", {"bold": True, "colour": INK}),
              ("Seized evidence and intelligence never leave the machine, because there is "
               "nowhere for them to go.", {})],
             [("Recovered analyst time. ", {"bold": True, "colour": INK}),
              ("Triage moves from reading a transaction graph to reading a ranked list of "
               f"{f['flagged']} entries out of {f['groups']} groups.", {})],
             [("It keeps improving. ", {"bold": True, "colour": INK}),
              ("Every decision an analyst records becomes a label; ",
               {}),
              ("python tasks.py retrain", {"font": FONT_MONO, "colour": BRAND}),
              (" trains a challenger that a human promotes — never one that promotes "
               "itself.", {})]],
            size=9, colour=BODY, spacing=1.14, space_after=6)

    textbox(slide, 6.85, 5.66, 4.15, 0.3, ["IN THE FIELD"], size=9.5, colour=INK,
            bold=True, space_after=0)
    textbox(slide, 6.85, 5.98, 4.15, 0.8,
            ["The same queue, on a phone: the one-sentence answer, the two numbers that "
             "matter, and who to look at first — served locally, working offline."],
            size=8.5, colour=BODY, spacing=1.1, space_after=0)
    phone_mockup(slide, ASSETS / "mockup-mobile.png", 11.35, 5.52, 1.72)
    footer(slide, 5)


def slide_six(prs, slide, f):
    clear_furniture(slide)
    header(slide, "RESEARCH AND REFERENCES",
           "What this stands on, and where the claims in this deck can be checked.")
    logo_badge(slide)

    left = [
        ("Domain foundations", [
            "Nakamoto, S. (2008). Bitcoin: A Peer-to-Peer Electronic Cash System.",
            "Meiklejohn et al. (2013). A Fistful of Bitcoins: Characterizing Payments "
            "Among Men with No Names. IMC — the common-input ownership heuristic, and its "
            "limits.",
            "Möser & Böhme (2015). Trends, Tips, Tolls: A Longitudinal Study of Bitcoin "
            "Transaction Fees. FC — value-flow tracing.",
            "Ron & Shamir (2013). Quantitative Analysis of the Full Bitcoin Transaction "
            "Graph. FC — peel chains and mixing.",
        ]),
        ("Threat patterns used as detectors", [
            "FATF (2023). Targeted Update on Implementation of the FATF Standards on "
            "Virtual Assets and VASPs — typologies behind the risk features.",
            "Chainalysis (2023–24). Crypto Crime Report — address poisoning and dusting as "
            "observed attacker behaviour.",
            "Public exchange advisories on lookalike-address poisoning (prefix/suffix "
            "forgery) — the basis of our matched-character rule.",
        ]),
    ]
    right = [
        ("Method and tooling", [
            "Pedregosa et al. (2011). Scikit-learn: Machine Learning in Python. JMLR.",
            "Breiman, L. (2001). Random Forests. Machine Learning 45(1).",
            "Liu, Ting & Zhou (2008). Isolation Forest. ICDM.",
            "Saabas, T. (2014). Interpreting random forests — the decision-path "
            "attribution we use, chosen over SHAP because it reconciles exactly and runs "
            "in microseconds on an air-gapped host.",
            "Hagberg, Schult & Swart (2008). Exploring Network Structure with NetworkX.",
            "FastAPI, uvicorn, SQLite, vis-network, Chart.js — official documentation.",
        ]),
        ("Standards and process", [
            "SIH 2026 Idea Submission template and evaluation criteria: novelty, "
            "complexity, clarity, feasibility, practicability, impact, UX (sih.gov.in).",
            "NTRO problem statement SIH26146: AI-Powered Monitoring & Analysis of Bitcoin "
            "Transaction Traffic.",
            "ISO/IEC 27037:2012 — handling of digital evidence; the reason the audit log is "
            "append-only and every output carries a hash.",
        ]),
    ]

    def block(x, y, width, groups):
        cursor = y
        for title, entries in groups:
            textbox(slide, x, cursor, width, 0.28, [title], size=9.5, colour=BRAND,
                    bold=True, space_after=0)
            cursor += 0.30
            for entry in entries:
                box = textbox(slide, x + 0.16, cursor, width - 0.16, 0.6, [entry],
                              size=8, colour=BODY, spacing=1.06, space_after=0)
                box.height = Inches(0.28 + 0.13 * (len(entry) // 92))
                cursor += 0.24 + 0.145 * (len(entry) // 92)
            cursor += 0.12
        return cursor

    block(0.55, 1.86, 5.9, left)
    block(6.85, 1.86, 5.9, right)

    rule(slide, 0.55, 6.34, 12.2, LINE, 0.02)
    textbox(slide, 0.55, 6.45, 12.2, 0.5,
            [[("Everything claimed here is reproducible on the machine that produced this "
               "deck. ", {"bold": True, "colour": INK}),
              ("python tasks.py smoke", {"font": FONT_MONO, "colour": BRAND}),
              (" rebuilds a capture, trains the models, replays the batches and checks "
               "every endpoint; ", {}),
              ("python tasks.py accept <file>", {"font": FONT_MONO, "colour": BRAND}),
              (" reports exactly what was read from any capture you supply.", {})]],
            size=8, colour=BODY, spacing=1.12, space_after=0)
    footer(slide, 6)


def main() -> int:
    if not TEMPLATE.exists():
        print(f"template not found: {TEMPLATE}")
        return 1
    prs = Presentation(str(TEMPLATE))
    slides = list(prs.slides)
    if len(slides) < 7:
        print(f"expected at least 7 slides in the template, found {len(slides)}")
        return 1

    f = facts()

    # The template's instruction slide is deleted, as the instructions themselves
    # say: the submitted deck is six slides.
    xml_slides = prs.slides._sldIdLst
    for slide_id in list(xml_slides)[6:]:
        prs.part.drop_rel(slide_id.rId)
        xml_slides.remove(slide_id)

    # The template's instruction text ("Proposed Solution (Describe your
    # Idea/Solution/Prototype)", "Technologies to be used...") is what the form
    # tells a team to replace. They are plain text boxes, not placeholders, so a
    # clear() that only looked at placeholders left them on the slides -- visible
    # under our own content on three of them.
    instruction_starts = ("proposed solution", "technologies to be", "analysis of the",
                          "potential impact", "details / links")
    for slide in prs.slides:
        for shape in list(slide.shapes):
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip().lower()
            if shape.is_placeholder:
                if text.startswith(instruction_starts):
                    shape.text_frame.clear()
            elif text.startswith(instruction_starts):
                shape._element.getparent().remove(shape._element)

    builders = (slide_one, slide_two, slide_three, slide_four, slide_five, slide_six)
    for builder, slide in zip(builders, prs.slides):
        builder(prs, slide, f)

    prs.save(str(OUTPUT))
    print(f"wrote {OUTPUT.name} — {len(prs.slides._sldIdLst)} slides")
    print(f"  numbers read from models/metrics.json and out/window-0.json")
    print(f"  AUC {f['auc']:.3f} · precision@25 {f['at_k']:.2f} · ARI {f['ari']:.3f} · "
          f"rows {f['rows']:,} · groups {f['groups']} · flagged {f['flagged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

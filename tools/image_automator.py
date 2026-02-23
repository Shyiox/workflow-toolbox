# Image Automator – Bildautomat (Apple-Style UI) | V4
# ---------------------------------------------------
# Automatisiert Bild-Optimierung + Zuschnitt 1:1 & 4:3 (Smart-Crop optional)
# Einheitliches Design im Schnabelhilfe-Look (Dark UI)
#
# Requirements:
#   pip install pillow
#
# Autor: ChatGPT (angepasst für Schnabelhilfe)

import os
import ctypes
import threading
import queue
import hashlib
import re
import json
import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageTk, ImageDraw
except ImportError as e:
    raise SystemExit(
        "Pillow ist nicht installiert. Bitte installiere es mit:\n\n"
        "pip install pillow\n"
    ) from e


# -----------------------------
# Branding / Config
# -----------------------------

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
APP_NAME = "Workflow – Bildautomat"
CONFIG_PATH = Path.home() / ".Workflow_config.json"


THEME = {
    "bg": "#1F2328",
    "card": "#252B32",
    "text": "#F7F3E8",
    "muted": "rgba(247,243,232,.78)",  # wird unten in Hex konvertiert
    "forest": "#A7C4A0",
    "accent": "#2AA7A1",
    "border": "rgba(167,196,160,.18)",  # wird unten in Hex konvertiert
    "shadow": "0 16px 44px rgba(0,0,0,.22)",  # Tk kann kein echtes Shadow, bleibt als Referenz
    "soft": "rgba(255,255,255,.04)",          # wird unten in Hex konvertiert
}


def _hex_to_rgb(h: str):
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _blend(bg_hex: str, fg_hex: str, alpha: float) -> str:
    """Alpha-Blending: fg über bg."""
    br, bg, bb = _hex_to_rgb(bg_hex)
    fr, fg, fb = _hex_to_rgb(fg_hex)
    r = int(round(br * (1 - alpha) + fr * alpha))
    g = int(round(bg * (1 - alpha) + fg * alpha))
    b = int(round(bb * (1 - alpha) + fb * alpha))
    return _rgb_to_hex((r, g, b))


def _rgba_to_hex(rgba: str, base_hex: str) -> str:
    """
    Konvertiert 'rgba(r,g,b,a)' zu Hex über base_hex, indem alpha geblendet wird.
    """
    m = re.match(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)", rgba.strip(), re.I)
    if not m:
        # fallback: falls schon hex, zurückgeben
        if rgba.strip().startswith("#"):
            return rgba.strip()
        return base_hex
    r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
    fg_hex = _rgb_to_hex((r, g, b))
    return _blend(base_hex, fg_hex, a)


THEME_HEX = {
    "bg": THEME["bg"],
    "card": THEME["card"],
    "text": THEME["text"],
    "forest": THEME["forest"],
    "accent": THEME["accent"],
    "muted": _rgba_to_hex(THEME["muted"], THEME["bg"]),
    "border": _rgba_to_hex(THEME["border"], THEME["card"]),
    "soft": _rgba_to_hex(THEME["soft"], THEME["card"]),
    # Praktische Zusatzfarben
    "log_bg": _blend(THEME["bg"], "#000000", 0.45),
    "danger": "#E25D5D",
    "ok": "#79D18C",
}


# -----------------------------
# Image Tools
# -----------------------------

def enhance_image(
    img: Image.Image,
    color: float,
    contrast: float,
    brightness: float,
    sharpness: float,
) -> Image.Image:
    """
    Klarer, freundlicher Look.
    - EXIF Rotation
    - Autokontrast
    - Farbe/Kontrast/Helligkeit
    - leichte Schärfung + UnsharpMask
    """
    img = ImageOps.exif_transpose(img)
    img = ImageOps.autocontrast(img, cutoff=1)

    img = ImageEnhance.Color(img).enhance(color)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Brightness(img).enhance(brightness)

    if abs(sharpness - 1.0) > 1e-3:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)

    img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=125, threshold=2))
    return img


def center_crop_to_aspect(img: Image.Image, target_aspect: float) -> Image.Image:
    """Sauberer Center-Crop (Fallback ohne Smart-Crop)."""
    w, h = img.size
    if w < 2 or h < 2:
        return img

    current = w / h
    if current >= target_aspect:
        new_w = int(round(h * target_aspect))
        new_h = h
    else:
        new_w = w
        new_h = int(round(w / target_aspect))

    new_w = max(2, min(new_w, w))
    new_h = max(2, min(new_h, h))

    x0 = (w - new_w) // 2
    y0 = (h - new_h) // 2
    return img.crop((x0, y0, x0 + new_w, y0 + new_h))


def _integral_image(gray_small: Image.Image):
    """Integralbild (Summed-Area Table) für schnelle Window-Summen."""
    w, h = gray_small.size
    px = gray_small.load()
    integral = [[0] * w for _ in range(h)]
    for y in range(h):
        row_sum = 0
        for x in range(w):
            row_sum += px[x, y]
            above = integral[y - 1][x] if y > 0 else 0
            integral[y][x] = row_sum + above
    return integral


def _rect_sum(integral, x, y, w, h):
    """Summe in einem Rechteck über Integralbild."""
    x2 = x + w - 1
    y2 = y + h - 1

    def at(xx, yy):
        if xx < 0 or yy < 0:
            return 0
        return integral[yy][xx]

    A = at(x2, y2)
    B = at(x - 1, y2)
    C = at(x2, y - 1)
    D = at(x - 1, y - 1)
    return A - B - C + D


def smart_crop_image(img: Image.Image, target_aspect: float, grid: int = 18) -> Image.Image:
    """
    Smart-Crop über Kantenenergie (FIND_EDGES) + Integralbild.
    Findet einen Bildausschnitt mit "mehr Inhalt" statt stumpf Mitte.
    """
    w, h = img.size
    if w < 2 or h < 2:
        return img

    current_aspect = w / h
    if current_aspect >= target_aspect:
        crop_h = h
        crop_w = int(round(h * target_aspect))
    else:
        crop_w = w
        crop_h = int(round(w / target_aspect))

    crop_w = max(2, min(crop_w, w))
    crop_h = max(2, min(crop_h, h))

    # kleiner rechnen
    max_side = 360
    scale = min(1.0, max_side / max(w, h))
    sw = max(2, int(round(w * scale)))
    sh = max(2, int(round(h * scale)))

    small = img.convert("L").resize((sw, sh), Image.Resampling.BILINEAR)
    edges = small.filter(ImageFilter.FIND_EDGES)
    integral = _integral_image(edges)

    cw = max(2, int(round(crop_w * scale)))
    ch = max(2, int(round(crop_h * scale)))
    cw = min(cw, sw)
    ch = min(ch, sh)

    if cw == sw and ch == sh:
        return img

    xs = [int(round(i * (sw - cw) / (grid - 1))) for i in range(grid)] if sw > cw else [0]
    ys = [int(round(i * (sh - ch) / (grid - 1))) for i in range(grid)] if sh > ch else [0]

    best_score = -10**18
    best_xy = (0, 0)

    # leichte Center-Priorisierung
    cx0 = (sw - cw) / 2
    cy0 = (sh - ch) / 2

    for y0 in ys:
        for x0 in xs:
            score = _rect_sum(integral, x0, y0, cw, ch)
            dist = ((x0 - cx0) ** 2 + (y0 - cy0) ** 2) ** 0.5
            score -= dist * 0.12
            if score > best_score:
                best_score = score
                best_xy = (x0, y0)

    x_s, y_s = best_xy
    x0 = int(round(x_s / scale))
    y0 = int(round(y_s / scale))

    x0 = max(0, min(x0, w - crop_w))
    y0 = max(0, min(y0, h - crop_h))

    return img.crop((x0, y0, x0 + crop_w, y0 + crop_h))


def resize_exact(img: Image.Image, target_w: int, target_h: int, no_upscale: bool) -> Image.Image:
    """Resizing auf exakt Zielmaße; optional ohne Upscaling."""
    if no_upscale and (img.size[0] < target_w or img.size[1] < target_h):
        return img
    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)


def save_as_jpg(img: Image.Image, out_path: Path, quality: int):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, quality=quality, optimize=True, progressive=True)


def save_as_png(img: Image.Image, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, optimize=True)


def list_images(input_dir: Path):
    files = []
    for root, _, fnames in os.walk(input_dir):
        for f in fnames:
            p = Path(root) / f
            if p.suffix.lower() in IMG_EXTS:
                files.append(p)
    return files


def safe_name(stem: str, max_len: int = 30) -> str:
    """Sichere Dateinamen + Kurz-Hash für echte Eindeutigkeit."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", stem).strip().strip(".")
    if not cleaned:
        cleaned = "image"
    h = hashlib.md5(stem.encode("utf-8", errors="ignore")).hexdigest()[:6]
    cleaned = cleaned[:max_len].rstrip(" ._-")
    return f"{cleaned}_{h}"


# -----------------------------
# Apple-ish UI Helpers
# -----------------------------

def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r=12, **kwargs):
    """
    Draw a rounded rectangle on a Canvas.
    """
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class ToggleSwitch(tk.Canvas):
    """
    iOS-like toggle switch (Canvas-based).
    """
    def __init__(self, master, variable: tk.BooleanVar, width=46, height=26, **kwargs):
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=THEME_HEX["card"],
            **kwargs
        )
        self.var = variable
        self.w = width
        self.h = height
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _event=None):
        self.var.set(not bool(self.var.get()))
        self._draw()

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())

        track_color = THEME_HEX["accent"] if on else _blend(THEME_HEX["card"], THEME_HEX["text"], 0.08)
        border_color = _blend(THEME_HEX["card"], THEME_HEX["text"], 0.18)
        knob_color = THEME_HEX["text"]
        shadow_color = _blend(THEME_HEX["card"], "#000000", 0.35)

        # Render at higher res and downsample for smoother edges (Tk canvas is not anti-aliased).
        scale = 3
        w = self.w * scale
        h = self.h * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = h // 2

        def to_rgba(hex_color, alpha=255):
            r0, g0, b0 = _hex_to_rgb(hex_color)
            return (r0, g0, b0, alpha)

        # Track (border + fill)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=to_rgba(border_color))
        inset = scale
        draw.rounded_rectangle(
            (inset, inset, w - 1 - inset, h - 1 - inset),
            radius=max(1, r - inset),
            fill=to_rgba(track_color),
        )

        # Knob
        pad = 3 * scale
        knob_d = h - pad * 2
        kx = (w - pad - knob_d) if on else pad
        ky = pad
        draw.ellipse(
            (kx + scale, ky + 2 * scale, kx + knob_d + scale, ky + knob_d + 2 * scale),
            fill=to_rgba(shadow_color),
        )
        draw.ellipse(
            (kx, ky, kx + knob_d, ky + knob_d),
            fill=to_rgba(knob_color),
        )

        img = img.resize((self.w, self.h), Image.Resampling.LANCZOS)
        self._img_ref = ImageTk.PhotoImage(img)
        self.create_image(0, 0, image=self._img_ref, anchor="nw")


class Card(tk.Frame):
    """Rounded card container (best-effort, border + padding)."""
    def __init__(self, master, title: str = "", **kwargs):
        super().__init__(master, bg=THEME_HEX["card"], **kwargs)
        self.configure(highlightthickness=1, highlightbackground=THEME_HEX["border"], highlightcolor=THEME_HEX["border"])
        self.title = title

        if title:
            lbl = tk.Label(
                self,
                text=title,
                bg=THEME_HEX["card"],
                fg=THEME_HEX["text"],
                font=("Segoe UI Variable", 11, "bold")
            )
            lbl.pack(anchor="w", padx=14, pady=(12, 0))

        self.body = tk.Frame(self, bg=THEME_HEX["card"])
        self.body.pack(fill="both", expand=True, padx=14, pady=12)


class Segmented(tk.Frame):
    """
    Simple segmented control (two or more segments).
    """
    def __init__(self, master, variable: tk.StringVar, values, **kwargs):
        super().__init__(master, bg=THEME_HEX["card"], **kwargs)
        self.var = variable
        self.values = values
        self.buttons = []
        for i, v in enumerate(values):
            b = tk.Button(
                self,
                text=v,
                command=lambda vv=v: self._set(vv),
                bd=0,
                relief="flat",
                padx=12,
                pady=6,
                font=("Segoe UI Variable", 9, "bold"),
                cursor="hand2",
            )
            b.pack(side="left", padx=(0 if i == 0 else 6, 0))
            self.buttons.append(b)
        self._sync()

    def _set(self, v):
        self.var.set(v)
        self._sync()

    def _sync(self):
        current = self.var.get()
        for b in self.buttons:
            is_active = (b.cget("text") == current)
            if is_active:
                b.configure(bg=THEME_HEX["accent"], fg=THEME_HEX["bg"])
            else:
                b.configure(bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06), fg=THEME_HEX["text"])


# -----------------------------
# App
# -----------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(1200, int(sw * 0.9))
        h = min(820, int(sh * 0.9))
        self.geometry(f"{w}x{h}")
        self.minsize(960, 680)
        self.configure(bg=THEME_HEX["bg"])

        self._logo_header = None
        self._logo_about = None
        self._icon_photos = []
        self._set_app_user_model_id()
        self._set_window_icon()

        style = ttk.Style(self)
        self.queue = queue.Queue()
        self.cancel_event = threading.Event()

        # State
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()

        self.base_var = tk.IntVar(value=1080)
        self.quality_var = tk.IntVar(value=80)

        self.export_1x1_var = tk.BooleanVar(value=True)
        self.export_4x3_var = tk.BooleanVar(value=True)

        self.edit_var = tk.BooleanVar(value=True)
        self.smartcrop_var = tk.BooleanVar(value=True)
        self.no_upscale_var = tk.BooleanVar(value=False)

        self.mode_43_var = tk.StringVar(value="Auto")  # Auto | Quer
        self.format_var = tk.StringVar(value="JPG")    # JPG | PNG
        self.preset_var = tk.StringVar(value="Tiervermittlung")  # Standard | Tiervermittlung

        self.color_var = tk.DoubleVar(value=1.12)
        self.contrast_var = tk.DoubleVar(value=1.08)
        self.brightness_var = tk.DoubleVar(value=1.06)
        self.sharpness_var = tk.DoubleVar(value=1.05)

        self.skip_existing_var = tk.BooleanVar(value=True)

        self._load_config()
        self._build_ui()
        self._fit_to_content()
        self.after(120, self._poll_queue)

    # ---------- Config ----------

    def _load_config(self):
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                self.base_var.set(int(data.get("base", 1080)))
                self.format_var.set(data.get("format", "JPG"))
                # JPEG defaults should always start at 80 unless user changes it in-session.
                if self.format_var.get().upper() == "JPG":
                    self.quality_var.set(80)
                else:
                    self.quality_var.set(int(data.get("quality", 80)))
                self.mode_43_var.set(data.get("mode_43", "Auto"))
                self.preset_var.set(data.get("preset", "Tiervermittlung"))
        except Exception:
            pass

    def _save_config(self):
        try:
            data = {
                "base": int(self.base_var.get()),
                "quality": int(self.quality_var.get()),
                "format": self.format_var.get(),
                "mode_43": self.mode_43_var.get(),
                "preset": self.preset_var.get(),
            }
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------- UI ----------

    def _build_ui(self):
        # Top bar
        self._logo_header = self._load_logo((80, 80))
        self._logo_about = self._load_logo((180, 180))

        top = tk.Frame(self, bg=THEME_HEX["bg"])
        top.pack(fill="x", padx=18, pady=(18, 10))

        title_row = tk.Frame(top, bg=THEME_HEX["bg"])
        title_row.pack(anchor="w")

        if self._logo_header:
            tk.Label(
                title_row,
                image=self._logo_header,
                bg=THEME_HEX["bg"],
            ).pack(side="left", padx=(0, 12))

        title = tk.Label(
            title_row,
            text="Workflow",
            bg=THEME_HEX["bg"],
            fg=THEME_HEX["text"],
            font=("Segoe UI Variable", 22, "bold"),
        )
        title.pack(side="left")

        sub = tk.Label(
            top,
            text="Bilder optimieren & zuschneiden – einheitlich, schnell, professionell (1:1 + 4:3).",
            bg=THEME_HEX["bg"],
            fg=_blend(THEME_HEX["text"], THEME_HEX["bg"], 0.35),
            font=("Segoe UI Variable", 10),
        )
        sub.pack(anchor="w", pady=(4, 0))

        # Layout: left settings / right log + preview
        content = tk.Frame(self, bg=THEME_HEX["bg"])
        content.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        left = tk.Frame(content, bg=THEME_HEX["bg"])
        left.pack(side="left", fill="y")

        right = tk.Frame(content, bg=THEME_HEX["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        # Cards on left
        self.card_paths = Card(left, "Ordner")
        self.card_paths.pack(fill="x", pady=(0, 12))

        self.card_export = Card(left, "Export")
        self.card_export.pack(fill="x", pady=(0, 12))

        self.card_look = Card(left, "Look (Preset)")
        self.card_look.pack(fill="x", pady=(0, 12))

        self.card_adv = Card(left, "Optionen")
        self.card_adv.pack(fill="x")

        # Right: Log + preview
        self.card_log = Card(right, "Aktivität")
        self.card_log.pack(fill="both", expand=True, pady=(0, 12))

        self.card_preview = Card(right, "Vorschau (erste Datei)")
        self.card_preview.pack(fill="x")

        # ---- Paths
        b = self.card_paths.body

        self._row_entry_button(b, "Input:", self.input_var, self._pick_input)

        # ---- Export
        b = self.card_export.body

        row2 = tk.Frame(b, bg=THEME_HEX["card"])
        row2.pack(fill="x", pady=(0, 10))
        tk.Label(row2, text="Format:", bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left")
        Segmented(row2, self.format_var, ["JPG", "PNG"]).pack(side="left", padx=10)

        row3 = tk.Frame(b, bg=THEME_HEX["card"])
        row3.pack(fill="x", pady=(0, 10))
        tk.Label(row3, text="Basis (1:1):", bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left")
        self.base_spin = tk.Spinbox(
            row3, from_=400, to=4000, textvariable=self.base_var,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"], insertbackground=THEME_HEX["text"],
            bd=0, relief="flat", width=8
        )
        self.base_spin.pack(side="left", padx=10, ipady=6)

        tk.Label(row3, text="JPEG-Qualität:", bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left", padx=(14, 0))
        self.quality_spin = tk.Spinbox(
            row3, from_=70, to=100, textvariable=self.quality_var,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"], insertbackground=THEME_HEX["text"],
            bd=0, relief="flat", width=6
        )
        self.quality_spin.pack(side="left", padx=10, ipady=6)
        # toggles export
        self._toggle_row(b, "1:1 Export", self.export_1x1_var)
        self._toggle_row(b, "4:3 Export", self.export_4x3_var)
        self._toggle_row(b, "Vorhandene Dateien überspringen", self.skip_existing_var)

        # ---- Look
        b = self.card_look.body

        rowp = tk.Frame(b, bg=THEME_HEX["card"])
        rowp.pack(fill="x", pady=(0, 10))
        tk.Label(rowp, text="Preset:", bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left")
        self.seg_preset = Segmented(rowp, self.preset_var, ["Standard", "Tiervermittlung"])
        self.seg_preset.pack(side="left", padx=10)
        # hook: update values when preset changes
        self.preset_var.trace_add("write", lambda *_: self._apply_preset())

        # Values grid
        grid = tk.Frame(b, bg=THEME_HEX["card"])
        grid.pack(fill="x")

        self._spin_pair(grid, "Farbe", self.color_var, 0.80, 1.40, 0.02, row=0, col=0)
        self._spin_pair(grid, "Kontrast", self.contrast_var, 0.80, 1.40, 0.02, row=0, col=1)
        self._spin_pair(grid, "Helligkeit", self.brightness_var, 0.80, 1.40, 0.02, row=1, col=0)
        self._spin_pair(grid, "Schärfe", self.sharpness_var, 0.80, 1.60, 0.02, row=1, col=1)

        hint = tk.Label(
            b,
            text="Tipp: 'Tiervermittlung' ist klar & freundlich. 'Standard' bleibt neutral.",
            bg=THEME_HEX["card"],
            fg=_blend(THEME_HEX["text"], THEME_HEX["bg"], 0.35),
            font=("Segoe UI Variable", 9),
        )
        hint.pack(anchor="w", pady=(10, 0))

        # ---- Options
        b = self.card_adv.body

        self._toggle_row(b, "Bild optimieren (Kontrast, Farbe, Schärfe)", self.edit_var)
        self._toggle_row(b, "Smart-Crop (besserer Ausschnitt)", self.smartcrop_var)
        self._toggle_row(b, "Nicht hochskalieren", self.no_upscale_var)

        rowm = tk.Frame(b, bg=THEME_HEX["card"])
        rowm.pack(fill="x", pady=(10, 0))
        tk.Label(rowm, text="4:3 Modus:", bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left")
        Segmented(rowm, self.mode_43_var, ["Auto", "Quer"]).pack(side="left", padx=10)

        # ---- Log area
        b = self.card_log.body

        self.progress = ttk.Progressbar(b, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 10))

        ctrl = tk.Frame(b, bg=THEME_HEX["card"])
        ctrl.pack(fill="x", pady=(0, 10))

        self.start_btn = tk.Button(
            ctrl, text="Start",
            command=self.start,
            bg=THEME_HEX["accent"], fg=THEME_HEX["bg"],
            bd=0, relief="flat",
            padx=18, pady=10,
            font=("Segoe UI Variable", 10, "bold"),
            cursor="hand2"
        )
        self.start_btn.pack(side="left")

        self.preview_btn = tk.Button(
            ctrl, text="Vorschau",
            command=self.show_preview,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06), fg=THEME_HEX["text"],
            bd=0, relief="flat",
            padx=18, pady=10,
            font=("Segoe UI Variable", 10, "bold"),
            cursor="hand2"
        )
        self.preview_btn.pack(side="left", padx=10)

        self.cancel_btn = tk.Button(
            ctrl, text="Abbrechen",
            command=self.cancel,
            bg=_blend(THEME_HEX["card"], THEME_HEX["danger"], 0.22), fg=THEME_HEX["text"],
            bd=0, relief="flat",
            padx=18, pady=10,
            font=("Segoe UI Variable", 10, "bold"),
            cursor="hand2",
            state="disabled"
        )
        self.cancel_btn.pack(side="left")

        self.status_lbl = tk.Label(
            ctrl,
            text="Bereit.",
            bg=THEME_HEX["card"],
            fg=_blend(THEME_HEX["text"], THEME_HEX["bg"], 0.35),
            font=("Segoe UI Variable", 10),
        )
        self.status_lbl.pack(side="right")

        self.log = tk.Text(
            b,
            height=16,
            wrap="word",
            bd=0,
            bg=THEME_HEX["log_bg"],
            fg=THEME_HEX["text"],
            insertbackground=THEME_HEX["text"],
            padx=10,
            pady=10,
        )
        self.log.pack(fill="both", expand=True)

        # ---- Preview strip (right, bottom)
        b = self.card_preview.body
        self.preview_info = tk.Label(
            b,
            text="Wähle einen Input-Ordner und klicke auf „Vorschau“.",
            bg=THEME_HEX["card"],
            fg=_blend(THEME_HEX["text"], THEME_HEX["bg"], 0.35),
            font=("Segoe UI Variable", 9),
        )
        self.preview_info.pack(anchor="w")

        self.preview_canvas = tk.Frame(b, bg=THEME_HEX["card"])
        self.preview_canvas.pack(fill="x", pady=(10, 0))

        self.preview_img_refs = []  # keep PhotoImage refs

        self._apply_preset()

        # Footer (bottom right)
        self.about_btn = tk.Button(
            self, text="Über Image Automator",
            command=self.show_about,
            bg=_blend(THEME_HEX["bg"], THEME_HEX["text"], 0.06), fg=THEME_HEX["text"],
            bd=0, relief="flat",
            padx=10, pady=6,
            font=("Segoe UI Variable", 9),
            cursor="hand2"
        )
        self.about_btn.place(relx=1.0, rely=1.0, x=-18, y=-14, anchor="se")

    def _fit_to_content(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        req_w = max(self.winfo_reqwidth(), 960)
        req_h = max(self.winfo_reqheight(), 680)
        w = min(req_w, int(sw * 0.96))
        h = min(req_h, int(sh * 0.96))
        self.geometry(f"{w}x{h}")

    def _resolve_asset(self, *parts):
        base = Path(__file__).resolve().parent
        return base.joinpath(*parts)

    def _set_app_user_model_id(self):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Image Automator.Bildautomat"
            )
        except Exception:
            pass

    def _set_window_icon(self):
        png_path = self._resolve_asset("Logo", "2.png")
        if png_path.exists():
            try:
                img = Image.open(png_path).convert("RGBA")
                sizes = (256, 128, 64, 32, 16)
                self._icon_photos = [
                    ImageTk.PhotoImage(img.resize((s, s), Image.Resampling.LANCZOS))
                    for s in sizes
                ]
                self.iconphoto(True, *self._icon_photos)
            except Exception:
                pass
        icon_path = self._resolve_asset("Logo", "2.ico")
        if icon_path.exists():
            try:
                if icon_path.stat().st_size > 1024:
                    self.iconbitmap(icon_path)
            except Exception:
                pass

    def _load_logo(self, size):
        logo_path = self._resolve_asset("Logo", "2.png")
        if not logo_path.exists():
            return None
        try:
            img = Image.open(logo_path).convert("RGBA")
            if size:
                img = img.resize(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _row_entry_button(self, master, label, var, cmd):
        row = tk.Frame(master, bg=THEME_HEX["card"])
        row.pack(fill="x", pady=(0, 10))
        tk.Label(row, text=label, bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(side="left")
        entry = tk.Entry(
            row,
            textvariable=var,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"],
            insertbackground=THEME_HEX["text"],
            bd=0,
            relief="flat",
        )
        entry.pack(side="left", fill="x", expand=True, padx=10, ipady=6)
        btn = tk.Button(
            row, text="Auswählen…",
            command=cmd,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"],
            bd=0, relief="flat",
            padx=12, pady=8,
            cursor="hand2",
            font=("Segoe UI Variable", 9, "bold")
        )
        btn.pack(side="right")

    def _toggle_row(self, master, text, var: tk.BooleanVar):
        row = tk.Frame(master, bg=THEME_HEX["card"])
        row.pack(fill="x", pady=(0, 10))
        tk.Label(
            row,
            text=text,
            bg=THEME_HEX["card"],
            fg=THEME_HEX["text"],
            font=("Segoe UI Variable", 10),
        ).pack(side="left")
        ToggleSwitch(row, var).pack(side="right")

    def _spin_pair(self, master, label, var: tk.DoubleVar, fmin, fmax, inc, row, col):
        cell = tk.Frame(master, bg=THEME_HEX["card"])
        cell.grid(row=row, column=col, padx=(0, 12 if col == 0 else 0), pady=(0, 10), sticky="ew")
        master.grid_columnconfigure(0, weight=1)
        master.grid_columnconfigure(1, weight=1)

        tk.Label(cell, text=label, bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(anchor="w")
        sp = tk.Spinbox(
            cell,
            from_=fmin, to=fmax, increment=inc,
            textvariable=var,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"],
            insertbackground=THEME_HEX["text"],
            bd=0, relief="flat",
            width=10
        )
        sp.pack(anchor="w", pady=(6, 0), ipady=6)

    # ---------- Presets ----------
    def _apply_preset(self):
        if self.preset_var.get() == "Tiervermittlung":
            self.color_var.set(1.12)
            self.contrast_var.set(1.08)
            self.brightness_var.set(1.06)
            self.sharpness_var.set(1.05)
        else:
            self.color_var.set(1.04)
            self.contrast_var.set(1.02)
            self.brightness_var.set(1.02)
            self.sharpness_var.set(1.00)
        self._save_config()

    # ---------- File picking ----------
    def _pick_input(self):
        d = filedialog.askdirectory(title="Input-Ordner wählen")
        if d:
            self.input_var.set(d)
            self._save_config()

    def _pick_output(self):
        d = filedialog.askdirectory(title="Output-Ordner wählen")
        if d:
            self.output_var.set(d)
            self._save_config()

    # ---------- Logging ----------
    def log_line(self, text: str):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    # ---------- Preview ----------
    def show_preview(self):
        self.preview_img_refs.clear()
        for w in self.preview_canvas.winfo_children():
            w.destroy()

        input_dir = Path(self.input_var.get()).expanduser()
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Fehler", "Bitte einen gültigen Input-Ordner auswählen.")
            return

        files = list_images(input_dir)
        if not files:
            messagebox.showinfo("Keine Bilder", "Im Input-Ordner wurden keine Bilder gefunden.")
            return

        f = files[0]
        try:
            with Image.open(f) as im:
                img = ImageOps.exif_transpose(im).copy()

            if self.edit_var.get():
                img = enhance_image(
                    img,
                    float(self.color_var.get()),
                    float(self.contrast_var.get()),
                    float(self.brightness_var.get()),
                    float(self.sharpness_var.get()),
                )

            w, h = img.size
            if self.mode_43_var.get() == "Quer":
                aspect_43 = 4 / 3
            else:
                aspect_43 = (4 / 3) if (w >= h) else (3 / 4)

            cropper = smart_crop_image if self.smartcrop_var.get() else center_crop_to_aspect

            thumbs = []

            if self.export_1x1_var.get():
                sq = cropper(img, 1.0)
                sq_t = sq.resize((220, 220), Image.Resampling.LANCZOS)
                thumbs.append(("1:1", sq_t))

            if self.export_4x3_var.get():
                c43 = cropper(img, aspect_43)
                # Vorschau: 4:3 immer quer angezeigt
                if aspect_43 >= 1.0:
                    c43_t = c43.resize((300, 225), Image.Resampling.LANCZOS)
                else:
                    c43_t = c43.resize((225, 300), Image.Resampling.LANCZOS)
                thumbs.append(("4:3", c43_t))

            if not thumbs:
                self.preview_info.configure(text="Aktiviere mindestens 1:1 oder 4:3 für die Vorschau.")
                return

            self.preview_info.configure(text=f"Beispiel: {f.name}")

            for label, im_thumb in thumbs:
                col = tk.Frame(self.preview_canvas, bg=THEME_HEX["card"])
                col.pack(side="left", padx=(0, 14))

                tk.Label(col, text=label, bg=THEME_HEX["card"], fg=THEME_HEX["text"], font=("Segoe UI Variable", 10, "bold")).pack(anchor="w", pady=(0, 6))

                ph = ImageTk.PhotoImage(im_thumb)
                self.preview_img_refs.append(ph)

                panel = tk.Label(col, image=ph, bg=THEME_HEX["card"])
                panel.pack()

        except Exception as e:
            messagebox.showerror("Vorschau fehlgeschlagen", str(e))

    def show_about(self):
        win = tk.Toplevel(self)
        win.title("Über Image Automator")
        win.configure(bg=THEME_HEX["card"])
        win.resizable(False, False)
        try:
            win.iconbitmap(self._resolve_asset("Logo", "2.ico"))
        except Exception:
            pass

        wrap = tk.Frame(win, bg=THEME_HEX["card"], padx=18, pady=16)
        wrap.pack(fill="both", expand=True)

        if self._logo_about:
            tk.Label(wrap, image=self._logo_about, bg=THEME_HEX["card"]).pack()

        info = (
            "Programmiert von:\n"
            "Nils Groon\n"
            "Großer Weidstückerweg 12\n"
            "68163 Mannheim\n"
            "n.groon@yahoo.de"
        )
        tk.Label(
            wrap,
            text=info,
            bg=THEME_HEX["card"],
            fg=THEME_HEX["text"],
            font=("Segoe UI Variable", 10),
            justify="center",
        ).pack(pady=(12, 0))

        tk.Button(
            wrap,
            text="OK",
            command=win.destroy,
            bg=_blend(THEME_HEX["card"], THEME_HEX["text"], 0.06),
            fg=THEME_HEX["text"],
            bd=0,
            relief="flat",
            padx=16,
            pady=8,
            font=("Segoe UI Variable", 9, "bold"),
            cursor="hand2",
        ).pack(pady=(14, 0))
    # ---------- Workflow ----------
    def start(self):
        input_dir = Path(self.input_var.get()).expanduser()

        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Fehler", "Bitte einen gültigen Input-Ordner auswählen.")
            return

        output_dir = input_dir / "Optimiert"
        output_dir.mkdir(parents=True, exist_ok=True)

        if not (self.export_1x1_var.get() or self.export_4x3_var.get()):
            messagebox.showerror("Fehler", "Bitte mindestens 1 Export-Format aktivieren (1:1 oder 4:3).")
            return

        files = list_images(input_dir)
        if not files:
            messagebox.showinfo("Keine Bilder", "Im Input-Ordner wurden keine Bilder gefunden.")
            return

        self.cancel_event.clear()
        self.start_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(maximum=len(files), value=0)
        self.status_lbl.configure(text=f"0 / {len(files)}")

        self.log.delete("1.0", "end")
        self.log_line(f"Gefunden: {len(files)} Bilder")
        self.log_line("Starte Verarbeitung...\n")

        args = {
            "files": files,
            "base": int(self.base_var.get()),
            "quality": int(self.quality_var.get()),
            "do_edit": bool(self.edit_var.get()),
            "use_smartcrop": bool(self.smartcrop_var.get()),
            "no_upscale": bool(self.no_upscale_var.get()),
            "mode_43": str(self.mode_43_var.get()),
            "color": float(self.color_var.get()),
            "contrast": float(self.contrast_var.get()),
            "brightness": float(self.brightness_var.get()),
            "sharpness": float(self.sharpness_var.get()),
            "output_dir": output_dir,
            "export_1x1": bool(self.export_1x1_var.get()),
            "export_4x3": bool(self.export_4x3_var.get()),
            "format": str(self.format_var.get()),
            "skip_existing": bool(self.skip_existing_var.get()),
        }

        self._save_config()

        t = threading.Thread(target=self._worker, kwargs=args, daemon=True)
        t.start()

    def cancel(self):
        self.cancel_event.set()
        self.log_line("\n[INFO] Abbruch angefordert...")

    def _make_session_dir(self, base_output: Path) -> Path:
        return base_output

    def _save_image(self, img: Image.Image, out_path: Path, fmt: str, quality: int):
        fmt = fmt.upper().strip()
        if fmt == "PNG":
            save_as_png(img, out_path.with_suffix(".png"))
        else:
            save_as_jpg(img, out_path.with_suffix(".jpg"), quality=quality)

    def _worker(
        self,
        files,
        base,
        quality,
        do_edit,
        use_smartcrop,
        no_upscale,
        mode_43,
        color,
        contrast,
        brightness,
        sharpness,
        output_dir,
        export_1x1,
        export_4x3,
        format,
        skip_existing,
    ):
        total = len(files)
        done = 0
        ok = 0
        fail = 0
        skipped = 0

        session_out = self._make_session_dir(output_dir)

        out_1x1 = session_out / "Quadratisch"
        out_4x3 = session_out / "Querformat"
        if export_1x1:
            out_1x1.mkdir(parents=True, exist_ok=True)
        if export_4x3:
            out_4x3.mkdir(parents=True, exist_ok=True)

        cropper = smart_crop_image if use_smartcrop else center_crop_to_aspect

        for idx, f in enumerate(files, start=1):
            if self.cancel_event.is_set():
                self.queue.put(("done_cancel", ok, fail, skipped, total, str(session_out)))
                return

            try:
                with Image.open(f) as im:
                    img = ImageOps.exif_transpose(im).copy()

                if do_edit:
                    img = enhance_image(img, color, contrast, brightness, sharpness)

                w, h = img.size

                if mode_43 == "Quer":
                    aspect_43 = 4 / 3
                else:
                    aspect_43 = (4 / 3) if (w >= h) else (3 / 4)

                img_sq = cropper(img, 1.0) if export_1x1 else None
                img_43 = cropper(img, aspect_43) if export_4x3 else None

                # Dateiname
                stem = safe_name(Path(f).stem)
                name_prefix = f"{idx:04d}_{stem}"

                # 1:1 Output
                if export_1x1 and img_sq is not None:
                    out_sq = resize_exact(img_sq, base, base, no_upscale=no_upscale)
                    p1 = out_1x1 / f"{name_prefix}_1x1"
                    if skip_existing and (p1.with_suffix(".jpg").exists() or p1.with_suffix(".png").exists()):
                        skipped += 1
                    else:
                        self._save_image(out_sq, p1, fmt=format, quality=quality)

                # 4:3 Output
                if export_4x3 and img_43 is not None:
                    if aspect_43 >= 1.0:
                        w43, h43 = int(round(base * (4 / 3))), base
                    else:
                        w43, h43 = base, int(round(base * (4 / 3)))

                    out_43 = resize_exact(img_43, w43, h43, no_upscale=no_upscale)
                    p2 = out_4x3 / f"{name_prefix}_4x3"
                    if skip_existing and (p2.with_suffix(".jpg").exists() or p2.with_suffix(".png").exists()):
                        skipped += 1
                    else:
                        self._save_image(out_43, p2, fmt=format, quality=quality)

                ok += 1

            except Exception as e:
                fail += 1
                self.queue.put(("line", f"[SKIP] {Path(f).name} -> {e}"))

            done += 1
            self.queue.put(("progress", done, total, Path(f).name))

        self.queue.put(("done_ok", ok, fail, skipped, total, str(session_out)))

    # ---------- Queue updates ----------
    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()

                if msg[0] == "progress":
                    done, total, name = msg[1], msg[2], msg[3]
                    self.progress.configure(value=done)
                    self.status_lbl.configure(text=f"{done} / {total}")
                    self.log_line(f"[OK] {name}")

                elif msg[0] == "line":
                    self.log_line(msg[1])

                elif msg[0] == "done_ok":
                    ok, fail, skipped, total, out_path = msg[1], msg[2], msg[3], msg[4], msg[5]
                    self.progress.configure(value=total)
                    self.status_lbl.configure(text=f"{total} / {total}")
                    self.log_line(f"\nFertig. OK: {ok}  Fehler: {fail}  Übersprungen: {skipped}")
                    self.log_line(f"Output: {out_path}")
                    self._reset_buttons()
                    messagebox.showinfo(
                        "Fertig",
                        f"Fertig.\nOK: {ok}\nFehler: {fail}\nÜbersprungen: {skipped}\n\nOutput:\n{out_path}",
                    )

                elif msg[0] == "done_cancel":
                    ok, fail, skipped, total, out_path = msg[1], msg[2], msg[3], msg[4], msg[5]
                    self.log_line(f"\nAbgebrochen. OK: {ok}  Fehler: {fail}  Übersprungen: {skipped}")
                    self.log_line(f"Teil-Output: {out_path}")
                    self._reset_buttons()
                    messagebox.showwarning(
                        "Abgebrochen",
                        f"Abgebrochen.\nOK: {ok}\nFehler: {fail}\nÜbersprungen: {skipped}\n\nOutput:\n{out_path}",
                    )

        except queue.Empty:
            pass

        self.after(120, self._poll_queue)

    def _reset_buttons(self):
        self.start_btn.configure(state="normal")
        self.preview_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            crash_path = Path.home() / "schnabellab_crash.log"
            crash_path.write_text(err, encoding="utf-8")
        except Exception:
            crash_path = None
        try:
            root = tk.Tk()
            root.withdraw()
            msg = "Ein Fehler ist aufgetreten."
            if crash_path:
                msg += f"\n\nLog: {crash_path}"
            messagebox.showerror("Image Automator", msg)
            root.destroy()
        except Exception:
            pass

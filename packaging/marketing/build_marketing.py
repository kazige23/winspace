"""Generate Xianyu listing assets for the "远程C盘清理 一对一服务" service.

Outputs (all inside packaging/marketing/):
    cover.png            — 800x800 thumbnail (the one that shows in search)
    image-1-pain.png     — 800x600 first detail image: "C盘满了"
    image-2-tool.png     — 800x600 second detail: winspace scan annotated
    image-3-process.png  — 800x600 third detail: 5-step service flow
    image-4-trust.png    — 800x600 fourth detail: guarantees

Designed for high CTR on Xianyu listings: bold red accents, big
Chinese characters, simple visual contrast (red "before" -> green
"after"), trust badges. The cover is the make-or-break asset — it
decides click-through. The detail images explain the offer to people
who clicked in.

Run from the repo root:
    .venv/Scripts/python.exe packaging/marketing/build_marketing.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
OUT = ROOT  # write next to this script

# --- font handling ---------------------------------------------------------

WIN_FONTS = Path("C:/Windows/Fonts")
FONT_BOLD = WIN_FONTS / "msyhbd.ttc"  # Microsoft YaHei Bold
FONT_REG = WIN_FONTS / "msyh.ttc"

if not FONT_BOLD.is_file():  # graceful fallback for non-Chinese Windows
    FONT_BOLD = WIN_FONTS / "segoeuib.ttf"
if not FONT_REG.is_file():
    FONT_REG = WIN_FONTS / "segoeui.ttf"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(str(path), size=size)


# --- colour palette --------------------------------------------------------

COL_BG = (250, 248, 244)  # warm off-white background
COL_TEXT = (40, 40, 40)
COL_SUB = (110, 110, 110)
COL_ACCENT = (227, 67, 56)  # Xianyu-style red
COL_ACCENT_DARK = (180, 40, 30)
COL_GREEN = (60, 165, 88)  # "after" success green
COL_GREEN_LIGHT = (220, 240, 226)
COL_RED_LIGHT = (252, 224, 220)
COL_GRAY = (200, 200, 200)
COL_WHITE = (255, 255, 255)


# --- helpers ---------------------------------------------------------------


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: int,
    y: int,
    width: int,
    f: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
) -> int:
    """Draw text horizontally centered inside [x, x+width]; return text height."""
    bbox = draw.textbbox((0, 0), text, font=f)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text((x + (width - text_w) // 2, y), text, font=f, fill=fill)
    return text_h


def draw_donut(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    radius: int,
    used_pct: float,
    used_fill: tuple[int, int, int],
    free_fill: tuple[int, int, int],
    label: str,
    label_font: ImageFont.FreeTypeFont,
    pct_font: ImageFont.FreeTypeFont,
) -> None:
    """A pie chart with a hole in the middle showing C-drive usage."""
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    # the "free" colour fills the whole pie first
    draw.ellipse(bbox, fill=free_fill)
    # used wedge on top
    end_angle = -90 + (360 * used_pct)
    draw.pieslice(bbox, start=-90, end=end_angle, fill=used_fill)
    # hole
    inner = radius - 35
    hole_bbox = (cx - inner, cy - inner, cx + inner, cy + inner)
    draw.ellipse(hole_bbox, fill=COL_WHITE)
    # percentage in the hole
    pct_text = f"{int(used_pct * 100)}%"
    bb = draw.textbbox((0, 0), pct_text, font=pct_font)
    pw = bb[2] - bb[0]
    ph = bb[3] - bb[1]
    draw.text(
        (cx - pw // 2, cy - ph),
        pct_text,
        font=pct_font,
        fill=used_fill,
    )
    # label below the pie
    bb = draw.textbbox((0, 0), label, font=label_font)
    lw = bb[2] - bb[0]
    draw.text(
        (cx - lw // 2, cy + radius + 12),
        label,
        font=label_font,
        fill=COL_TEXT,
    )


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    length: int,
    color: tuple[int, int, int],
) -> None:
    body_h = 14
    tip = 26
    draw.rectangle(
        (x, y - body_h // 2, x + length - tip, y + body_h // 2), fill=color
    )
    draw.polygon(
        [
            (x + length - tip, y - tip),
            (x + length, y),
            (x + length - tip, y + tip),
        ],
        fill=color,
    )


def draw_pill(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: tuple[int, int, int],
    border: tuple[int, int, int] | None = None,
) -> None:
    r = h // 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=fill, outline=border)


# --- cover.png -------------------------------------------------------------


def build_cover() -> Path:
    W, H = 800, 800
    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)

    # red top band
    band_h = 36
    draw.rectangle((0, 0, W, band_h), fill=COL_ACCENT)
    draw.text(
        (24, 4),
        "闲鱼专享 · 远程上门技术服务",
        font=font(22, bold=True),
        fill=COL_WHITE,
    )

    # main title — biggest text on the canvas
    draw_centered_text(
        draw,
        "远程C盘清理",
        x=0,
        y=70,
        width=W,
        f=font(96, bold=True),
        fill=COL_ACCENT,
    )
    draw_centered_text(
        draw,
        "一对一陪同 · 半小时搞定",
        x=0,
        y=190,
        width=W,
        f=font(40, bold=True),
        fill=COL_TEXT,
    )

    # before / after donuts
    donut_y = 360
    donut_r = 110
    draw_donut(
        draw,
        cx=200,
        cy=donut_y,
        radius=donut_r,
        used_pct=0.95,
        used_fill=COL_ACCENT,
        free_fill=COL_RED_LIGHT,
        label="清理前 · 95%",
        label_font=font(26, bold=True),
        pct_font=font(46, bold=True),
    )
    draw_donut(
        draw,
        cx=600,
        cy=donut_y,
        radius=donut_r,
        used_pct=0.55,
        used_fill=COL_GREEN,
        free_fill=COL_GREEN_LIGHT,
        label="清理后 · 55%",
        label_font=font(26, bold=True),
        pct_font=font(46, bold=True),
    )
    draw_arrow(draw, x=340, y=donut_y, length=120, color=COL_TEXT)

    # the key promise — big number people can latch onto
    draw_centered_text(
        draw,
        "平均释放 10–30 GB",
        x=0,
        y=540,
        width=W,
        f=font(50, bold=True),
        fill=COL_ACCENT_DARK,
    )

    # trust bullet row
    pills = [
        ("数据零损失", COL_GREEN),
        ("可一键还原", COL_ACCENT),
        ("全程录屏", COL_TEXT),
    ]
    pill_w = 200
    pill_h = 56
    spacing = 16
    total = len(pills) * pill_w + (len(pills) - 1) * spacing
    x = (W - total) // 2
    for label, color in pills:
        draw_pill(draw, x=x, y=640, w=pill_w, h=pill_h, fill=COL_WHITE, border=color)
        # text centered in pill
        f = font(26, bold=True)
        bb = draw.textbbox((0, 0), label, font=f)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        draw.text(
            (x + (pill_w - tw) // 2, 640 + (pill_h - th) // 2 - 4),
            label,
            font=f,
            fill=color,
        )
        x += pill_w + spacing

    # bottom strip — what's in it (literally)
    draw.rectangle((0, H - 50, W, H), fill=COL_TEXT)
    draw_centered_text(
        draw,
        "浏览器缓存 · pip / npm / cargo · 游戏库 · 临时文件",
        x=0,
        y=H - 40,
        width=W,
        f=font(20),
        fill=COL_WHITE,
    )

    out = OUT / "cover.png"
    img.save(out, "PNG", optimize=True)
    return out


# --- image 1: pain point ---------------------------------------------------


def build_image_pain() -> Path:
    W, H = 800, 600
    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)

    # title
    draw_centered_text(
        draw,
        "你是不是有这些烦恼?",
        x=0,
        y=40,
        width=W,
        f=font(48, bold=True),
        fill=COL_ACCENT,
    )

    # red C drive visualisation
    bar_x, bar_y, bar_w, bar_h = 100, 160, 600, 60
    # background
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=8,
        fill=(230, 230, 230),
    )
    # red fill at 92%
    used = int(bar_w * 0.92)
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + used, bar_y + bar_h),
        radius=8,
        fill=COL_ACCENT,
    )
    draw.text(
        (bar_x + 14, bar_y + 14),
        "C:  本地磁盘 已用 92%",
        font=font(28, bold=True),
        fill=COL_WHITE,
    )

    # pain bullets
    pains = [
        "● C 盘满了,装个软件都没空间",
        "● 不敢乱删,怕动到系统或软件",
        "● 用了 360 / Dism++ 还是不够干净",
        "● 浏览器、游戏库占了几十个 G 不知道在哪里",
        "● 一删就出问题,数据没了找不回来",
    ]
    y = 280
    f = font(28)
    for line in pains:
        draw.text((100, y), line, font=f, fill=COL_TEXT)
        y += 48

    # bottom hook
    draw_centered_text(
        draw,
        "→ 找专业的人远程帮你看,问题搞定",
        x=0,
        y=540,
        width=W,
        f=font(28, bold=True),
        fill=COL_GREEN,
    )

    out = OUT / "image-1-pain.png"
    img.save(out, "PNG", optimize=True)
    return out


# --- image 2: the tool we use (annotated screenshot) -----------------------


def build_image_tool() -> Path:
    """Embed the GUI screenshot with arrow callouts pointing at key bits."""
    base_path = ROOT.parent.parent / "dev-artifacts" / "screenshot-main-v3.png"
    if not base_path.is_file():
        # Fall back to a placeholder if the screenshot is missing.
        screenshot = Image.new("RGB", (1100, 600), (40, 40, 40))
    else:
        screenshot = Image.open(base_path).convert("RGB")

    W, H = 800, 600
    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)

    draw_centered_text(
        draw,
        "用我们自研工具,智能识别可清理目录",
        x=0,
        y=24,
        width=W,
        f=font(28, bold=True),
        fill=COL_TEXT,
    )

    # Embed screenshot, scaled to fit a 700-wide box.
    target_w = 720
    scale = target_w / screenshot.width
    target_h = int(screenshot.height * scale)
    scaled = screenshot.resize((target_w, target_h), Image.Resampling.LANCZOS)

    sx = (W - target_w) // 2
    sy = 80
    # frame
    draw.rounded_rectangle(
        (sx - 4, sy - 4, sx + target_w + 4, sy + target_h + 4),
        radius=6,
        outline=COL_ACCENT,
        width=2,
    )
    img.paste(scaled, (sx, sy))

    # callouts at the bottom
    callouts = [
        ("浏览器缓存", COL_ACCENT),
        ("包管理器(npm/pip)", COL_ACCENT),
        ("Steam 游戏库", COL_ACCENT),
        ("node_modules", COL_ACCENT),
    ]
    y = sy + target_h + 24
    f = font(22, bold=True)
    col_w = (W - 80) // len(callouts)
    x = 40
    for label, color in callouts:
        bb = draw.textbbox((0, 0), label, font=f)
        tw = bb[2] - bb[0]
        draw.rectangle((x, y, x + col_w - 12, y + 44), outline=color, width=2)
        draw.text((x + (col_w - 12 - tw) // 2, y + 8), label, font=f, fill=color)
        x += col_w

    out = OUT / "image-2-tool.png"
    img.save(out, "PNG", optimize=True)
    return out


# --- image 3: service flow -------------------------------------------------


def build_image_process() -> Path:
    W, H = 800, 600
    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)

    draw_centered_text(
        draw,
        "5 步流程 · 30 分钟搞定",
        x=0,
        y=30,
        width=W,
        f=font(40, bold=True),
        fill=COL_ACCENT,
    )

    steps = [
        ("1", "拍下后", "加微信 / QQ 联系"),
        ("2", "远程连接", "向日葵 / ToDesk 连入你的电脑"),
        ("3", "扫描分析", "运行专业工具,识别可清理目录"),
        ("4", "陪同确认", "告诉你每一项是什么,你点头我才动"),
        ("5", "确认完成", "通常释放 10–30 GB,你看着才收货"),
    ]
    y = 110
    num_f = font(36, bold=True)
    title_f = font(26, bold=True)
    desc_f = font(22)
    for num, title, desc in steps:
        # circle with number
        cx, cy, r = 60, y + 28, 28
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COL_ACCENT)
        bb = draw.textbbox((0, 0), num, font=num_f)
        nw = bb[2] - bb[0]
        nh = bb[3] - bb[1]
        draw.text(
            (cx - nw // 2, cy - nh // 2 - 6), num, font=num_f, fill=COL_WHITE
        )
        # text
        draw.text((120, y + 8), title, font=title_f, fill=COL_TEXT)
        draw.text((120, y + 44), desc, font=desc_f, fill=COL_SUB)
        y += 90

    out = OUT / "image-3-process.png"
    img.save(out, "PNG", optimize=True)
    return out


# --- image 4: trust + guarantee --------------------------------------------


def build_image_trust() -> Path:
    W, H = 800, 600
    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)

    draw_centered_text(
        draw,
        "我们的保障",
        x=0,
        y=40,
        width=W,
        f=font(48, bold=True),
        fill=COL_ACCENT,
    )

    cards = [
        ("数据零损失", "工具有 9 步反向保护,每次操作前先复制+校验,你看着确认才执行 rmtree"),
        ("可一键还原", "30 天内随时撤销,我们记录所有操作日志,任意一步都能回退"),
        ("全程录屏", "整个远程过程录屏,你可以提前要,出问题就是证据"),
        ("不删用户数据", "云盘 / 微信 / QQ / iCloud 等用户数据所在目录全部硬黑名单,工具自动拒绝触碰"),
    ]
    y = 130
    title_f = font(28, bold=True)
    desc_f = font(20)
    for title, desc in cards:
        # green check icon
        cx, cy, r = 60, y + 30, 22
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COL_GREEN)
        draw.line(
            [(cx - 10, cy + 2), (cx - 2, cy + 12), (cx + 12, cy - 8)],
            fill=COL_WHITE,
            width=4,
        )
        draw.text((110, y + 4), title, font=title_f, fill=COL_TEXT)
        draw.text((110, y + 44), desc, font=desc_f, fill=COL_SUB)
        y += 100

    # CTA bottom
    draw.rectangle((0, H - 60, W, H), fill=COL_ACCENT)
    draw_centered_text(
        draw,
        "拍下后 24 小时内联系 · 不满意全额退",
        x=0,
        y=H - 48,
        width=W,
        f=font(26, bold=True),
        fill=COL_WHITE,
    )

    out = OUT / "image-4-trust.png"
    img.save(out, "PNG", optimize=True)
    return out


# --- main ------------------------------------------------------------------


def main() -> None:
    print(f"Output directory: {OUT}")
    print(f"Cover:    {build_cover()}")
    print(f"Image 1:  {build_image_pain()}")
    print(f"Image 2:  {build_image_tool()}")
    print(f"Image 3:  {build_image_process()}")
    print(f"Image 4:  {build_image_trust()}")


if __name__ == "__main__":
    main()

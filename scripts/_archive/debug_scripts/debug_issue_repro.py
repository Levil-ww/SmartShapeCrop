"""Reproduce the corner issues reported by user on synthetic images."""
import os
from PIL import Image, ImageDraw
from core.image_cropper import apply_border_only_corners

OUT_DIR = r"D:\SmartShapeCrop\debug_output"
os.makedirs(OUT_DIR, exist_ok=True)

def save(name, img):
    img.save(os.path.join(OUT_DIR, name), quality=95)

# Common params
DPI = 150

def make_zhongguyulin():
    """Case A: black outer bg, white inner, black border."""
    w, h = 1200, 1000
    img = Image.new('RGB', (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 60
    # white inner area
    draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], fill=(255, 255, 255))
    # black border at inner edge
    border = 12
    draw.rectangle([margin, margin, w - margin - 1, h - margin - 1],
                   outline=(0, 0, 0), width=border)
    # some content
    draw.text((w // 2, h // 2), "Own Story", fill=(0, 0, 0), anchor="mm")
    return img

def make_sainaishiguang():
    """Case B: beige outer bg, black border, white inner panels."""
    w, h = 1600, 1000
    beige = (245, 235, 220)
    img = Image.new('RGB', (w, h), beige)
    draw = ImageDraw.Draw(img)
    margin = 40
    # outer black border
    draw.rectangle([margin, margin, w - margin - 1, h - margin - 1],
                   outline=(25, 22, 20), width=10)
    # inner white panels area
    draw.rectangle([margin + 60, margin + 60, w - margin - 61, h - margin - 61],
                   fill=(255, 255, 255))
    # black separator lines between panels
    for x in [w // 3, 2 * w // 3]:
        draw.line([(x, margin + 60), (x, h - margin - 61)], fill=(0, 0, 0), width=6)
    return img

def make_qingwumanyeborder():
    """Case C: green bg, dotted white border."""
    w, h = 1200, 900
    green = (60, 90, 45)
    img = Image.new('RGB', (w, h), green)
    draw = ImageDraw.Draw(img)
    margin = 50
    # two dotted white border lines
    def dotted_rect(x0, y0, x1, y1, color, step=6):
        # top
        for x in range(x0, x1, step * 2):
            draw.line([(x, y0), (min(x + step, x1), y0)], fill=color, width=3)
        # bottom
        for x in range(x0, x1, step * 2):
            draw.line([(x, y1), (min(x + step, x1), y1)], fill=color, width=3)
        # left
        for y in range(y0, y1, step * 2):
            draw.line([(x0, y), (x0, min(y + step, y1))], fill=color, width=3)
        # right
        for y in range(y0, y1, step * 2):
            draw.line([(x1, y), (x1, min(y + step, y1))], fill=color, width=3)

    dotted_rect(margin, margin, w - margin - 1, h - margin - 1, (255, 255, 255))
    dotted_rect(margin + 18, margin + 18, w - margin - 19, h - margin - 19, (255, 255, 255))
    # floral content near corners
    draw.ellipse([margin + 80, margin + 80, margin + 180, margin + 180], fill=(255, 255, 255))
    draw.ellipse([w - margin - 180, h - margin - 180, w - margin - 80, h - margin - 80], fill=(255, 255, 255))
    return img

def run():
    # Case A bottom-left/bottom-right radius 1.5cm
    img_a = make_zhongguyulin()
    save("caseA_source.jpg", img_a)
    r_a = 1.5
    res_a = apply_border_only_corners(img_a, {'bl': r_a, 'br': r_a}, dpi=DPI, bg_color=(255, 255, 255))
    save("caseA_current.jpg", res_a)

    # Case B all corners radius 5cm
    img_b = make_sainaishiguang()
    save("caseB_source.jpg", img_b)
    r_b = 5.0
    res_b = apply_border_only_corners(img_b, {'tl': r_b, 'tr': r_b, 'bl': r_b, 'br': r_b}, dpi=DPI, bg_color=(255, 255, 255))
    save("caseB_current.jpg", res_b)

    # Case C all corners radius 3.5cm
    img_c = make_qingwumanyeborder()
    save("caseC_source.jpg", img_c)
    r_c = 3.5
    res_c = apply_border_only_corners(img_c, {'tl': r_c, 'tr': r_c, 'bl': r_c, 'br': r_c}, dpi=DPI, bg_color=(255, 255, 255))
    save("caseC_current.jpg", res_c)

    print("Done. Outputs in", OUT_DIR)

if __name__ == "__main__":
    run()

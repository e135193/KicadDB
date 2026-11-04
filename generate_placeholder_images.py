#!/usr/bin/env python3
"""Generate placeholder images for README.md"""
from PIL import Image, ImageDraw, ImageFont
import os

OUTPUT_DIR = r"C:\Users\Engin\Desktop\kicad DB\docs\images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Color scheme
BG_COLOR = (30, 30, 40)
ACCENT_COLOR = (0, 180, 216)
TEXT_COLOR = (220, 220, 230)
SECONDARY_COLOR = (120, 120, 140)
BORDER_COLOR = (60, 60, 80)

def create_image(filename, title, subtitle="", width=800, height=500):
    img = Image.new('RGB', (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Draw border
    draw.rectangle([0, 0, width-1, height-1], outline=BORDER_COLOR, width=2)
    
    # Draw title
    try:
        font_title = ImageFont.truetype("arial.ttf", 36)
        font_sub = ImageFont.truetype("arial.ttf", 20)
    except:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
    
    # Center title
    bbox = draw.textbbox((0, 0), title, font=font_title)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) // 2, (height - th) // 2 - 30), title, fill=ACCENT_COLOR, font=font_title)
    
    # Center subtitle
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((width - tw) // 2, (height + th) // 2 + 10), subtitle, fill=SECONDARY_COLOR, font=font_sub)
    
    # Add decorative elements
    # Top bar
    draw.rectangle([0, 0, width, 40], fill=(25, 25, 35))
    draw.text((20, 8), "KicadDB", fill=ACCENT_COLOR, font=font_sub)
    
    # Bottom status bar
    draw.rectangle([0, height-30, width, height], fill=(25, 25, 35))
    draw.text((20, height-25), "v2.0  |  Python/Tkinter  |  SQLite", fill=SECONDARY_COLOR, font=font_sub)
    
    # Side panel indicator
    draw.rectangle([0, 40, 200, height-30], fill=(35, 35, 45))
    draw.line([(200, 40), (200, height-30)], fill=BORDER_COLOR, width=1)
    
    # Mock content area
    content_x, content_y = 220, 60
    content_w, content_h = width - 240, height - 120
    
    # Draw mock table rows
    for i in range(5):
        y = content_y + i * 60
        color = (45, 45, 55) if i % 2 == 0 else (40, 40, 50)
        draw.rectangle([content_x, y, content_x + content_w, y + 45], fill=color, outline=BORDER_COLOR)
        draw.text((content_x + 20, y + 10), f"Row {i+1}: Component data...", fill=TEXT_COLOR, font=font_sub)
    
    img.save(os.path.join(OUTPUT_DIR, filename))
    print(f"Created: {filename}")

# Generate all placeholder images
images = [
    ("logo.png", "KicadDB", "Component & BOM Management", 400, 400),
    ("product_tree.png", "Product Tree", "Hierarchical Project Browser", 1000, 600),
    ("bom_editor.png", "BOM Editor", "Inline Editing with Component Matching", 1000, 600),
    ("revision_compare.png", "Revision Compare", "Side-by-side Diff View", 1000, 600),
    ("lcsc_manager.png", "LCSC Manager", "Browse, Preview & Convert Parts", 1000, 600),
    ("footprint_preview.png", "Footprint Preview", "2D SVG with KiCad Palette", 800, 600),
    ("step_preview.png", "3D STEP Preview", "Interactive Rotation & Colors", 800, 600),
]

for filename, title, subtitle, w, h in images:
    create_image(filename, title, subtitle, w, h)

print(f"\nAll images generated in {OUTPUT_DIR}")
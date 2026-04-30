from PIL import Image, ImageDraw, ImageFont
import os

def create_icon(filename="app_icon.ico"):
    # Create a 256x256 image with transparent background
    size = (256, 256)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Draw a rounded rectangle background (Blue)
    rect_coords = [10, 10, 246, 246]
    draw.rounded_rectangle(rect_coords, radius=40, fill="#228be6", outline="#1864ab", width=5)

    # Draw the letter "S" (for SEAAP) or a robot look
    # Let's draw a simple "S" shape or similar abstract tech shape
    # Since we don't have a font file guaranteed, we'll draw shapes.
    
    # Draw a stylized "S" using polygons/lines
    # Top bar
    draw.rectangle([60, 60, 196, 90], fill="white")
    # Middle bar
    draw.rectangle([60, 113, 196, 143], fill="white")
    # Bottom bar
    draw.rectangle([60, 166, 196, 196], fill="white")
    
    # Left vertical (top)
    draw.rectangle([60, 60, 90, 143], fill="white")
    # Right vertical (bottom)
    draw.rectangle([166, 113, 196, 196], fill="white")

    # Save as ICO
    image.save(filename, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Icon created: {filename}")

if __name__ == "__main__":
    create_icon()

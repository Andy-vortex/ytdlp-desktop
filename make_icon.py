from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent
image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((8, 8, 248, 248), radius=56, fill='#3478ee')
draw.rounded_rectangle((108, 47, 148, 147), radius=9, fill='white')
draw.polygon(((67, 119), (128, 180), (189, 119), (166, 96), (128, 135), (90, 96)), fill='white')
draw.rounded_rectangle((61, 193, 195, 211), radius=8, fill='#b8e9ff')
image.save(root / 'app.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

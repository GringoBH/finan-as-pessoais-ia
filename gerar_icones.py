import math
import os
from PIL import Image, ImageDraw, ImageFont

PASTA = os.path.dirname(os.path.abspath(__file__))
S = 1024

def achar_fonte(tamanho):
    for caminho in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()

img = Image.new("RGB", (S, S))
px = img.load()
topo, fundo = (15, 118, 110), (6, 78, 59)
for y in range(S):
    t = y / (S - 1)
    cor = tuple(int(topo[i] + (fundo[i] - topo[i]) * t) for i in range(3))
    for x in range(S):
        px[x, y] = cor
d = ImageDraw.Draw(img)

# barras
base, w = 850, 95
for x0, h in ((570, 120), (690, 210), (810, 300)):
    d.rounded_rectangle((x0, base - h, x0 + w, base), radius=24, fill="#99f6e4")

# seta
x1, y1, x2, y2 = 590, 640, 850, 330
d.line((x1, y1, x2, y2), fill="#fbbf24", width=30)
d.ellipse((x1 - 15, y1 - 15, x1 + 15, y1 + 15), fill="#fbbf24")
ang = math.atan2(y2 - y1, x2 - x1)
def ponto(a, l):
    return (x2 + l * math.cos(a), y2 + l * math.sin(a))
ponta = [(x2 + 40 * math.cos(ang), y2 + 40 * math.sin(ang)),
         ponto(ang + math.radians(150), 95), ponto(ang - math.radians(150), 95)]
d.polygon(ponta, fill="#fbbf24")

# cofrinho
ox = 25
rosa, escuro = "#f9a8d4", "#ec4899"
d.rounded_rectangle((200 + ox, 730, 255 + ox, 815), radius=18, fill=escuro)
d.rounded_rectangle((340 + ox, 730, 395 + ox, 815), radius=18, fill=escuro)
d.polygon([(335 + ox, 545), (400 + ox, 495), (415 + ox, 570)], fill=escuro)
d.arc((100 + ox, 610, 165 + ox, 680), start=90, end=330, fill=escuro, width=14)
d.ellipse((130 + ox, 520, 470 + ox, 775), fill=rosa)
d.ellipse((430 + ox, 605, 515 + ox, 695), fill=escuro)
d.ellipse((462 + ox, 632, 474 + ox, 650), fill="#9d174d")
d.ellipse((480 + ox, 632, 492 + ox, 650), fill="#9d174d")
d.ellipse((395 + ox, 600, 419 + ox, 624), fill="#500724")
d.rounded_rectangle((265 + ox, 545, 345 + ox, 562), radius=8, fill="#9d174d")

# moeda
cx, cy, r = 305 + ox, 350, 105
d.ellipse((cx - r - 10, cy - r - 10, cx + r + 10, cy + r + 10), fill="#d97706")
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#fbbf24")
d.ellipse((cx - r + 22, cy - r + 22, cx + r - 22, cy + r - 22), outline="#f59e0b", width=8)
fonte = achar_fonte(150)
b = d.textbbox((0, 0), "€", font=fonte)
d.text((cx - (b[2] - b[0]) / 2 - b[0], cy - (b[3] - b[1]) / 2 - b[1]), "€", font=fonte, fill="#78350f")

for tam in (192, 512):
    img.resize((tam, tam), Image.LANCZOS).save(os.path.join(PASTA, f"icon-{tam}.png"))
print("Ícones criados em:", PASTA)
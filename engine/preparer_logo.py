"""
Detoure la marque du projet pour qu'elle tienne sur le fond noir de l'interface.

`logo/logoH.png` est livre sur fond blanc, avec un bloc de texte sombre sous
l'embleme. Pose tel quel dans une barre laterale noire, il y dessine un carre
blanc, et son texte marine y devient illisible.

Ce script en derive `logo/logo_mark.png` : l'embleme seul - la premiere bande
d'encre du fichier, avant l'espace qui la separe du texte - avec le blanc rendu
transparent. L'original n'est jamais modifie.

    .venv/Scripts/python.exe engine/preparer_logo.py
"""
from __future__ import annotations

import pathlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "logo" / "logoH.png"
CIBLE = ROOT / "logo" / "logo_mark.png"

# Seuils de detourage, en luminance. Au-dela du premier, le pixel est du fond ;
# en deca du second, c'est de l'encre ; entre les deux, le degrade rend les
# bords lisses au lieu de les crenter.
FOND = 248.0
ENCRE = 228.0
COTE = 512


def luminance(pixel) -> float:
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def bande_de_l_embleme(img: Image.Image) -> tuple[int, int, int, int]:
    """Cadre la premiere zone encree, celle qui precede le bloc de texte."""
    largeur, hauteur = img.size
    px = img.load()
    encre_par_ligne = [
        sum(1 for x in range(0, largeur, 3) if luminance(px[x, y]) < ENCRE)
        for y in range(hauteur)]

    haut = next(y for y, n in enumerate(encre_par_ligne) if n)
    bas, vide = haut, 0
    for y in range(haut, hauteur):
        if encre_par_ligne[y]:
            bas, vide = y, 0
        else:
            vide += 1
            if vide > 20:            # l'espace typographique avant le texte
                break

    encre_par_colonne = [
        sum(1 for y in range(haut, bas, 3) if luminance(px[x, y]) < ENCRE)
        for x in range(largeur)]
    gauche = next(x for x, n in enumerate(encre_par_colonne) if n)
    droite = largeur - 1 - next(i for i, n in enumerate(reversed(encre_par_colonne)) if n)
    marge = 12
    return (max(0, gauche - marge), max(0, haut - marge),
            min(largeur, droite + marge), min(hauteur, bas + marge))


def detourer(img: Image.Image) -> Image.Image:
    px = img.load()
    largeur, hauteur = img.size
    for y in range(hauteur):
        for x in range(largeur):
            r, v, b, a = px[x, y]
            lum = luminance((r, v, b))
            if lum >= FOND:
                alpha = 0
            elif lum <= ENCRE:
                alpha = 255
            else:
                alpha = int(255 * (FOND - lum) / (FOND - ENCRE))
            px[x, y] = (r, v, b, min(a, alpha))
    return img


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    embleme = detourer(source.crop(bande_de_l_embleme(source)))
    cote = max(embleme.size)
    carre = Image.new("RGBA", (cote, cote), (0, 0, 0, 0))
    carre.paste(embleme, ((cote - embleme.size[0]) // 2,
                          (cote - embleme.size[1]) // 2))
    carre.resize((COTE, COTE), Image.LANCZOS).save(CIBLE)
    print(f"{CIBLE.relative_to(ROOT)} ecrit ({COTE}x{COTE}, fond transparent)")


if __name__ == "__main__":
    main()

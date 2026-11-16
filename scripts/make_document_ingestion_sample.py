"""Build a synthetic three-page fixture. Requires ReportLab, pypdfium2 and Pillow."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from pypdfium2 import PdfDocument
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

SIZE = (520, 325)


def draw_page(canvas, title, chinese, english):
    canvas.setFillColor(HexColor("#18354c"))
    canvas.setFont("Helvetica-Bold", 23)
    canvas.drawString(42, 255, title)
    canvas.setStrokeColor(HexColor("#96b9be"))
    canvas.line(42, 230, 478, 230)
    canvas.setFont("STSong-Light", 23)
    canvas.drawString(42, 185, chinese)
    canvas.setFont("Helvetica", 20)
    canvas.drawString(42, 140, english)
    canvas.showPage()


def main():
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    with TemporaryDirectory(prefix="dq-fixture-") as directory:
        prefix = Path(directory) / "scan"
        stencil = Canvas(str(prefix.with_suffix(".pdf")), pagesize=SIZE, invariant=True)
        draw_page(stencil, "Scanned verification page", "扫描样本：订单数量", "Order count: 48")
        stencil.save()
        with PdfDocument(prefix.with_suffix(".pdf")) as pdf:
            pdf[0].render(scale=220 / 72).to_pil().save(prefix.with_suffix(".png"))
        sample = Canvas(str(output), pagesize=SIZE, invariant=True)
        draw_page(sample, "Native verification page", "本地样本：订单数量", "Order count: 24")
        sample.drawImage(str(prefix.with_suffix(".png")), 0, 0, *SIZE)
        sample.showPage()
        sample.showPage()
        sample.save()


if __name__ == "__main__":
    main()

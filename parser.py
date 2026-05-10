from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from docx import Document
from pptx import Presentation


def _to_binary_stream(file: bytes | BinaryIO) -> BytesIO | BinaryIO:
    if isinstance(file, bytes):
        return BytesIO(file)
    return file


def parse_ppt(file: bytes | BinaryIO) -> str:
    """
    提取 PPT 全部幻灯片文本，并按页面顺序拼接。
    过滤少于 5 个字符的碎片文本。
    """
    stream = _to_binary_stream(file)
    presentation = Presentation(stream)

    slide_texts: list[str] = []
    for slide in presentation.slides:
        fragments: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            raw_text = shape.text.strip()
            if len(raw_text) < 5:
                continue
            fragments.append(raw_text)

        if fragments:
            slide_texts.append("\n".join(fragments))

    return "\n\n".join(slide_texts).strip()


def parse_docx(file: bytes | BinaryIO) -> str:
    """
    提取 Word 教案全文。
    """
    stream = _to_binary_stream(file)
    doc = Document(stream)

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(paragraphs).strip()

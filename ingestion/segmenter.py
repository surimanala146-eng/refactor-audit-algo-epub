"""Segmentation : texte de référence -> chapitres -> phrases positionnées."""
from __future__ import annotations

import re
from dataclasses import dataclass

_CHAPTER = re.compile(
    r"^[ \t]*(?i:chapitre|chapter|partie|section|livre|annexe)\b.{0,80}$"
    r"|^[ \t]*[A-ZÉÈÊÀÂÎÔÛÇ0-9][^a-zà-öø-ÿ\n]{5,80}$",
    re.MULTILINE,
)
_SENT_END = re.compile(r"(?<=[.!?…])[\s\n]+")


@dataclass(frozen=True)
class Sentence:
    chapter: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Chapter:
    title: str
    start: int
    end: int


def split_chapters(text: str) -> list[Chapter]:
    marks = [(m.start(), m.group().strip()) for m in _CHAPTER.finditer(text)]
    marks = [m for m in marks if 3 <= len(m[1]) <= 90]
    if not marks:
        return [Chapter("Texte intégral", 0, len(text))]
    chapters: list[Chapter] = []
    if marks[0][0] > 0:
        chapters.append(Chapter("Préambule", 0, marks[0][0]))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chapters.append(Chapter(title, pos, end))
    return chapters


def prepare_book(text: str) -> tuple[list[Chapter], list[Sentence]]:
    """Chapitres + phrases, positions exprimées dans le texte de référence."""
    chapters = split_chapters(text)
    sentences: list[Sentence] = []
    for ch in chapters:
        body = text[ch.start : ch.end]
        offset = ch.start
        cursor = 0
        for piece in _SENT_END.split(body):
            if not piece:
                continue
            idx = body.find(piece, cursor)
            if idx < 0:
                continue
            cursor = idx + len(piece)
            stripped = piece.strip()
            if not stripped:
                continue
            s_start = offset + idx + piece.find(stripped)
            sentences.append(
                Sentence(ch.title, stripped, s_start, s_start + len(stripped))
            )
    return chapters, sentences

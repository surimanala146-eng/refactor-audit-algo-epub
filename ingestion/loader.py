"""Chargement des sources et intégrité cryptographique.

Règle automatique : tout fichier illisible ou vide est IGNORÉ avec une trace
dans le manifest (clé "sources_ignorees"). Jamais de question à l'utilisateur.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from domain.models import Source, slugify

SUFFIXES = (".txt", ".md", ".epub", ".pdf")
_TITLE_AUTHOR = re.compile(r"^(?P<title>.+?)\s*[\(\[]\s*(?P<author>[^)\]]+?)\s*[\)\]]")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def _read_epub(path: Path) -> str:
    from ebooklib import epub, ITEM_DOCUMENT  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore

    book = epub.read_epub(str(path))
    parts = [
        BeautifulSoup(it.get_content(), "html.parser").get_text("\n")
        for it in book.get_items()
        if it.get_type() == ITEM_DOCUMENT
    ]
    return "\n\n".join(parts)


def _read_pdf(path: Path) -> str:
    import fitz  # type: ignore

    with fitz.open(str(path)) as doc:
        return "\n\n".join(page.get_text() for page in doc)


def read_reference_text(path: Path) -> str:
    """Texte de référence normalisé (celui qui sera hashé et indexé)."""
    suffix = path.suffix.lower()
    if suffix == ".epub":
        raw = _read_epub(path)
    elif suffix == ".pdf":
        raw = _read_pdf(path)
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
    return normalize_text(raw)


def guess_author_title(stem: str) -> tuple[str, str]:
    m = _TITLE_AUTHOR.match(stem.strip())
    if m:
        return m.group("author").strip(), m.group("title").strip()
    cleaned = re.sub(r"[_]+", " ", stem).strip()
    return "Inconnu", cleaned or stem


def load_source(path: Path, used_ids: set[str] | None = None) -> tuple[Source, str]:
    """Construit une Source figée + son texte de référence."""
    text = read_reference_text(path)
    author, title = guess_author_title(path.stem)
    base = slugify(f"{title}-{author}" if author != "Inconnu" else title)
    book_id = base
    if used_ids is not None:
        n = 2
        while book_id in used_ids:
            book_id = f"{base}_{n}"
            n += 1
        used_ids.add(book_id)
    data = text.encode("utf-8")
    src = Source(
        book_id=book_id,
        path=str(path.resolve()),
        author=author,
        title=title,
        sha256=sha256_bytes(data),
        size=len(data),
    )
    return src, text


def reload_verified(source: Source) -> str:
    """Recharge le texte et re-vérifie le SHA256 (preuve auditable)."""
    text = read_reference_text(Path(source.path))
    digest = sha256_bytes(text.encode("utf-8"))
    if digest != source.sha256:
        raise ValueError(
            f"SHA256 divergent pour {source.book_id}: attendu {source.sha256}, obtenu {digest}"
        )
    return text

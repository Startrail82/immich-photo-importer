"""Herkennen welke bestanden foto's of video's zijn."""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff",
        ".webp", ".heic", ".heif", ".avif",
        ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".m2ts", ".wmv"}
)

# Bestanden die we stil negeren (systeembestanden, sidecars, tijdelijke bestanden).
IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
IGNORED_SUFFIXES = frozenset({".xmp", ".partial", ".tmp", ".part", ".crdownload"})


def classify(path: Path) -> str:
    """Geeft 'image', 'video', 'ignored' of 'unknown' terug."""
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in IGNORED_NAMES or name.startswith("._") or suffix in IGNORED_SUFFIXES:
        return "ignored"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"

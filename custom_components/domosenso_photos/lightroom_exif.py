"""EXIF tags built from Lightroom metadata; normalize writes only these (the rendition's own EXIF is dropped)."""
from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from .lightroom_catalog import Photo

EXIF_IFD = 0x8769
_STAMP = '%Y:%m:%d %H:%M:%S'
_MAX_DENOMINATOR = 100000  # EXIF rationals are two 32-bit longs; keep them small


def _rational(value: float) -> IFDRational:
    ratio = Fraction(value).limit_denominator(_MAX_DENOMINATOR)
    return IFDRational(ratio.numerator, ratio.denominator)


def exif_adder(photo: Photo) -> Callable[[Image.Exif], None]:
    """Return a function that writes the photo's date and camera tags; GPS is never written."""
    meta = photo.meta

    def add(exif: Image.Exif) -> None:
        stamp = photo.captured.strftime(_STAMP)
        sub = exif.get_ifd(EXIF_IFD)
        exif[306] = stamp  # DateTime
        sub[36867] = stamp  # DateTimeOriginal
        if meta.make:
            exif[271] = meta.make
        if meta.model:
            exif[272] = meta.model
        if meta.lens:
            sub[0xA434] = meta.lens
        if meta.focal_mm is not None:
            sub[0x920A] = _rational(meta.focal_mm)
        if meta.f_number is not None:
            sub[0x829D] = _rational(meta.f_number)
        if meta.exposure_s is not None:
            sub[0x829A] = _rational(meta.exposure_s)
        if meta.focal35_mm is not None:
            sub[0xA405] = int(round(meta.focal35_mm))
        if meta.iso is not None:
            sub[0x8827] = int(meta.iso)

    return add

"""osxphotos template functions: name files by local capture time.

osxphotos' built-in `{created}` is the library's `date`, which Photos stores as UTC with no
timezone for many older or imported items. Those names come out hours off, sometimes on the
wrong day or in the wrong month. `date_original` is the EXIF capture time in local time, which
is what the archive's names have always used. When it is the same instant as `date`, it is
just the better rendering of it, so use it. When it isn't (e.g. an old file whose EXIF time is
the import date), fall back to `date`.

These functions are called by osxphotos with a PhotoInfo, as `{function:naming.py::name}`.
"""

# `date` is stored to the second and `date_original` to the millisecond (and Photos sometimes truncates
# one and rounds the other), so one instant can differ by up to a couple of seconds.
SAME_INSTANT_SECONDS = 2


def choose(date, original):
    """The datetime to name a file from, given a photo's `date` and `date_original`.

    The UTC problem is when `date` is in a different timezone from the local `date_original` but is the
    same instant. Then use `date_original`'s local wall clock. When both have the same UTC offset there is
    nothing to fix, so keep `date` exactly (names stay identical to the old `{created}` ones). When
    `date_original` is a different instant altogether (e.g. an import date), also keep `date`.
    """
    if (date and original and date.utcoffset() != original.utcoffset()
            and abs((date - original).total_seconds()) <= SAME_INSTANT_SECONDS):
        return original
    return date


def capture_datetime(photo):
    return choose(photo.date, photo.date_original)


def capture_name(photo, **kwargs):
    return capture_datetime(photo).strftime("%Y-%m-%d_%H-%M-%S")


def capture_month(photo, **kwargs):
    return capture_datetime(photo).strftime("%Y-%m")

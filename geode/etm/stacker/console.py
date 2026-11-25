"""
Console formatting for an EtmStacker run.

A stacker run is a sequence of distinct phases -- read the station list, resolve the
earthquake catalogue, build every station's ETM, assemble the shared system, solve,
report -- but until these helpers existed every one of them wrote free-form ' -- ' lines
into one flat stream, interleaved across eight worker processes, with no way to tell
where one phase ended and the next began or which lines were results rather than
progress. This module exists to give that stream a shape: numbered phase banners, one
prefix per kind of message, and aligned tables for anything with more than two columns.

Nothing here decides what is worth printing. Routine per-station progress belongs in the
logger (visible with -etm_verbosity info), warnings and results belong here; the call
sites make that choice and these helpers only format it.

Everything goes through tqdm.write so output stays legible while a progress bar is live.
"""

from typing import Iterable, List, Sequence

from tqdm import tqdm

from ...Utils import print_yellow

WIDTH = 78

# Phase numbering is per-run and assigned in call order rather than declared up front, so
# that a run which skips a phase (no sparse stations, a loaded session) stays
# consecutively numbered instead of showing gaps.
_phase_number = 0


def reset_phases() -> None:
    """Start numbering again -- call once at the top of a run."""
    global _phase_number
    _phase_number = 0


def phase(title: str) -> None:
    """Open a numbered phase: a blank line, a rule, the title, a rule."""
    global _phase_number
    _phase_number += 1
    tqdm.write('')
    tqdm.write('=' * WIDTH)
    tqdm.write(f'  {_phase_number}. {title.upper()}')
    tqdm.write('=' * WIDTH)


def step(title: str) -> None:
    """A named step inside the current phase."""
    tqdm.write('')
    tqdm.write(f'-- {title} ' + '-' * max(0, WIDTH - len(title) - 4))


def info(message: str) -> None:
    tqdm.write(f'   {message}')


def result(label: str, value: str) -> None:
    """A single labelled result, column-aligned with its neighbours."""
    tqdm.write(f'   {label:.<42} {value}')


def warn(message: str) -> None:
    tqdm.write(print_yellow(f'   ! {message}'))


def table(headers: Sequence[str], rows: Iterable[Sequence[str]],
          align: str = '') -> None:
    """
    Print an aligned table. `align` is one character per column, 'l' or 'r' (default
    left for the first column, right for the rest, which is what every table here
    wants: a name followed by numbers).
    """
    rows = [[str(c) for c in row] for row in rows]
    if not rows:
        return
    headers = [str(h) for h in headers]
    if not align:
        align = 'l' + 'r' * (len(headers) - 1)

    widths = [max(len(headers[i]), max(len(r[i]) for r in rows)) for i in range(len(headers))]

    def fmt(cells: List[str]) -> str:
        return '   ' + '  '.join(c.rjust(w) if align[i] == 'r' else c.ljust(w)
                                 for i, (c, w) in enumerate(zip(cells, widths)))

    tqdm.write(fmt(headers))
    tqdm.write('   ' + '  '.join('-' * w for w in widths))
    for row in rows:
        tqdm.write(fmt(row))


def columns(items: Sequence[str], per_row: int = 8, indent: str = '   ') -> None:
    """Print a long list of short names in aligned columns."""
    if not items:
        return
    width = max(len(i) for i in items)
    for i in range(0, len(items), per_row):
        tqdm.write(indent + '  '.join(s.ljust(width) for s in items[i:i + per_row]).rstrip())

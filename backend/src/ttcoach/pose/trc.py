"""Minimal TRC (OpenSim marker file) parser for Sports2D pixel output (DESIGN §8)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


class TrcError(ValueError):
    pass


@dataclass(frozen=True)
class TrcData:
    header: dict[str, str]  # DataRate, NumFrames, ...
    markers: tuple[str, ...]
    frame: np.ndarray  # int64 [rows], raw Frame# column
    time: np.ndarray  # float64 [rows]
    xyz: np.ndarray  # float64 [rows, n_markers, 3], NaN = missing

    @property
    def num_frames(self) -> int:
        return int(float(self.header.get("NumFrames", len(self.frame))))

    @property
    def data_rate(self) -> float:
        return float(self.header.get("DataRate", "nan"))


def _cell(value: str) -> float:
    value = value.strip()
    if not value:
        return np.nan
    try:
        return float(value)
    except ValueError:
        return np.nan  # "nan", "NaN" and junk all map to missing


def parse_trc(path: Path) -> TrcData:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 5:
        raise TrcError(f"{path}: too short for a TRC file")
    keys = lines[1].split("\t")
    values = lines[2].split("\t")
    header = {k.strip(): v.strip() for k, v in zip(keys, values, strict=False) if k.strip()}
    names = lines[3].split("\t")
    if len(names) < 2 or names[0].strip() != "Frame#" or names[1].strip() != "Time":
        raise TrcError(f"{path}: line 4 must start with Frame#, Time")
    markers = tuple(n.strip() for n in names[2:] if n.strip())
    n_markers = len(markers)
    width = 2 + 3 * n_markers

    frames, times, rows = [], [], []
    for lineno, line in enumerate(lines[5:], start=6):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < width:
            cells += [""] * (width - len(cells))
        try:
            frames.append(int(float(cells[0])))
            times.append(float(cells[1]))
        except ValueError:
            raise TrcError(f"{path}:{lineno}: bad Frame#/Time") from None
        rows.append([_cell(c) for c in cells[2:width]])

    xyz = np.array(rows, dtype=np.float64).reshape(len(rows), n_markers, 3)
    return TrcData(
        header=header,
        markers=markers,
        frame=np.array(frames, dtype=np.int64),
        time=np.array(times, dtype=np.float64),
        xyz=xyz,
    )

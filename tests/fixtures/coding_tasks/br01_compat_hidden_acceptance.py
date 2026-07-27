"""Compatibility-profile acceptance and functional score for BeRicher BR-01 v2."""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd


def _candidate(repository: Path) -> object:
    source = (repository / "src/components/data_fetcher_tushare.py").read_text(encoding="utf-8")
    marker = "if 'f_ann_date' in df.columns:"
    start = source.rfind("\n", 0, source.index(marker)) + 1
    end = source.index("                    # 4. 保存", start)
    block = textwrap.dedent(source[start:end])
    namespace: dict[str, object] = {"pd": pd}
    exec("def apply(df):\n" + textwrap.indent(block, "    ") + "\n    return df", namespace)
    return namespace["apply"]


@contextmanager
def _legacy_mixed_comparison_guard() -> Iterator[None]:
    """Model the old deployment failure on string Series compared with missing values."""
    names = ("__ge__", "__gt__", "__le__", "__lt__")
    originals = {name: getattr(pd.Series, name) for name in names}
    original_astype = pd.Series.astype
    original_frame_max = pd.DataFrame.max

    def guarded(name: str) -> object:
        original = originals[name]

        def compare(series: pd.Series, other: object) -> object:
            if isinstance(other, pd.Series) and (series.isna().any() or other.isna().any()):
                raise TypeError("legacy pandas rejects string/NaN Series comparison")
            return original(series, other)

        return compare

    try:
        def legacy_astype(
            series: pd.Series, dtype: object, *args: object, **kwargs: object
        ) -> object:
            result = original_astype(series, dtype, *args, **kwargs)
            if dtype is str or dtype == "str":
                return result.fillna("nan")
            return result

        def legacy_frame_max(
            frame: pd.DataFrame, axis: object = 0, *args: object, **kwargs: object
        ) -> object:
            if axis in (1, "columns") and frame.isna().any().any():
                raise TypeError("legacy pandas rejects mixed string/NaN row-wise max")
            return original_frame_max(frame, axis, *args, **kwargs)

        pd.Series.astype = legacy_astype
        pd.DataFrame.max = legacy_frame_max
        for name in names:
            setattr(pd.Series, name, guarded(name))
        yield
    finally:
        pd.Series.astype = original_astype
        pd.DataFrame.max = original_frame_max
        for name, original in originals.items():
            setattr(pd.Series, name, original)


def _dates(apply: object, *, include_f_ann: bool = True) -> list[str]:
    values: dict[str, list[object]] = {
        "ann_date": ["20240105", "20240106", "20240107"],
    }
    if include_f_ann:
        values["f_ann_date"] = ["20240104", None, "20240109"]
    result = apply(pd.DataFrame(values))
    return result["pit_value_date"].astype(str).tolist()


def main(repository: Path) -> int:
    apply = _candidate(repository)
    criteria: list[dict[str, object]] = []

    def check(name: str, points: int, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - report all independent criteria
            criteria.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            criteria.append({"name": name, "points": points, "earned": points})

    def current_semantics() -> None:
        assert _dates(apply) == ["20240105", "20240106", "20240109"]

    def legacy_compatibility() -> None:
        with _legacy_mixed_comparison_guard():
            assert _dates(apply) == ["20240105", "20240106", "20240109"]

    def absent_column_fallback() -> None:
        assert _dates(apply, include_f_ann=False) == [
            "20240105",
            "20240106",
            "20240107",
        ]

    check("pit_date_semantics", 40, current_semantics)
    check("legacy_string_nan_compatibility", 50, legacy_compatibility)
    check("absent_f_ann_fallback", 10, absent_column_fallback)
    earned = sum(int(item["earned"]) for item in criteria)
    print(
        "TRACELANE_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": criteria}, sort_keys=True)
    )
    if earned != 100:
        return 1
    print("BR-01 v2 compatibility acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))

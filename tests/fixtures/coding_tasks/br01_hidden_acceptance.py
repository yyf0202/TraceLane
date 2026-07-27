"""Independent acceptance check for the BR-01 coding-task pilot.

This file is deliberately kept outside the target repository.  The target
deployment still uses a pandas version where row-wise ``max`` over string
dates plus float NaN fails, so the check locks the compatibility requirement
as well as the intended date semantics.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pandas as pd


def pit_dates(repository: Path) -> list[str]:
    source = (repository / "src/components/data_fetcher_tushare.py").read_text(encoding="utf-8")
    marker = "if 'f_ann_date' in df.columns:"
    start = source.rfind("\n", 0, source.index(marker)) + 1
    end = source.index("                    # 4. 保存", start)
    block = textwrap.dedent(source[start:end])
    namespace: dict[str, object] = {"pd": pd}
    exec("def apply(df):\n" + textwrap.indent(block, "    ") + "\n    return df", namespace)
    frame = pd.DataFrame(
        {
            "ann_date": ["20240105", "20240106", "20240107"],
            "f_ann_date": ["20240104", None, "20240109"],
        }
    )
    result = namespace["apply"](frame)
    return result["pit_value_date"].astype(str).tolist()


def requires_nan_safe_implementation(repository: Path) -> None:
    source = (repository / "src/components/data_fetcher_tushare.py").read_text(encoding="utf-8")
    start = source.index("if 'f_ann_date' in df.columns:")
    end = source.index("                    # 4. 保存", start)
    block = source[start:end]
    assert ".fillna(" in block or ".isna()" in block, "missing f_ann_date must explicitly fall back"
    assert "[['ann_date', 'f_ann_date']].max(axis=1)" not in block, (
        "mixed string/NaN row-wise max is incompatible with the deployment pandas version"
    )


if __name__ == "__main__":
    requires_nan_safe_implementation(Path(sys.argv[1]))
    actual = pit_dates(Path(sys.argv[1]))
    expected = ["20240105", "20240106", "20240109"]
    assert actual == expected, (actual, expected)
    print("BR-01 independent acceptance passed")

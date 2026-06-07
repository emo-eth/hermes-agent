from __future__ import annotations

import ast
from pathlib import Path


def test_emo_author_override_is_not_duplicated() -> None:
    release_source = Path("scripts/release.py").read_text()
    module = ast.parse(release_source)

    author_map = next(
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "AUTHOR_MAP"
    )
    assert isinstance(author_map, ast.Dict)

    emo_key_count = sum(
        1
        for key in author_map.keys
        if isinstance(key, ast.Constant) and key.value == "emodoteth@gmail.com"
    )

    assert emo_key_count == 1

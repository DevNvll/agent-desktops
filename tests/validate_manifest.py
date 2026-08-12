from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "omarchy/nullskies.agent-desktops"
MANIFEST = PLUGIN / "manifest.json"


def main() -> None:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == 1
    assert value["id"] == "nullskies.agent-desktops"
    assert set(value["kinds"]) == {"service", "bar-widget"}
    assert value["barWidget"]["defaultSection"] == "left"

    entries = value["entryPoints"]
    assert entries["service"] == "Service.qml"
    assert entries["barWidget"] == "Widget.qml"
    for relative in entries.values():
        path = Path(relative)
        assert not path.is_absolute()
        assert ".." not in path.parts
        assert (PLUGIN / path).is_file()


if __name__ == "__main__":
    main()

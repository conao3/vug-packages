"""Stage the store indexes for a public vug cache export."""

import argparse
import json
from pathlib import Path
import re
import tomllib


EXCLUDED = {
    "1password-cli",
    "claude-agent-acp",
    "linux-firmware-atheros-wifi",
    "linux-firmware-broadcom-wifi",
    "linux-firmware-chelsio",
    "linux-firmware-intel-wifi",
    "linux-firmware-mediatek-wifi",
    "linux-firmware-realtek",
    "linux-firmware-realtek-wifi",
    "tree-sitter-lpf",
    "tree-sitter-supercollider",
}
STORE_ID = re.compile(r"[a-z2-7]{32}")


def output_ids(index, contents):
    if index == "index":
        key, output_id = contents.split()
        if not STORE_ID.fullmatch(key):
            raise ValueError("Invalid build key")
        ids = {output_id}
    else:
        ids = {output["id"] for output in json.loads(contents)["outputs"].values()}
    if not ids or any(not STORE_ID.fullmatch(output_id) for output_id in ids):
        raise ValueError("Invalid store ID")
    return ids


def stage(data, repository, destination):
    entries = []
    excluded_ids = set()
    for index in ("index", "index-outputs"):
        for source in sorted((data / index).glob("*/*")):
            package = source.parent.name
            if not source.is_file() or source.name.startswith("."):
                continue
            if package not in EXCLUDED:
                shard = package[:2] if len(package) > 1 else package + "_"
                record = repository / "packages" / shard / package / "versions" / f"{source.name}.toml"
                if not record.is_file():
                    continue
                with record.open("rb") as file:
                    version = tomllib.load(file)
                if version.get("source", {}).get("url", "").startswith("git+ssh:"):
                    excluded_ids.update(output_ids(index, source.read_text()))
                    continue
                if bool(version.get("outputs")) != (index == "index-outputs"):
                    continue
            contents = source.read_bytes()
            ids = output_ids(index, contents.decode())
            if package in EXCLUDED:
                excluded_ids.update(ids)
            else:
                entries.append((source.relative_to(data), contents, ids))

    destination.mkdir()
    (destination / "store").symlink_to(data.resolve() / "store", target_is_directory=True)
    for path, contents, ids in entries:
        if ids & excluded_ids:
            continue
        output = destination / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(contents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    stage(args.data_dir, args.root, args.directory)


if __name__ == "__main__":
    main()

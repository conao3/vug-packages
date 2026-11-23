"""Prune and publish complete, signed vug cache exports."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import NamedTemporaryFile


ID = re.compile(r"[a-z2-7]{32}")
SIGNATURE = re.compile(r"[a-f0-9]{128}")
KEY = re.compile(r"(?:keys|output-keys)/[a-z2-7]{32}")
ARCHIVE = re.compile(r"paths/[a-z2-7]{32}\.cpio(?:\.zst)?")
DEFAULT_LIMIT = 9_000_000_000


class CacheError(Exception):
    pass


def generation(root):
    objects = {}
    for directory in ("keys", "output-keys"):
        for file in (root / directory).glob("*"):
            name = f"{directory}/{file.name}"
            if not KEY.fullmatch(name) or file.is_symlink() or not file.is_file():
                raise CacheError("Invalid cache key path")
            payload, signature = file.read_text(encoding="utf-8").splitlines()
            if not SIGNATURE.fullmatch(signature):
                raise CacheError(f"Missing or malformed signature for {name}")
            if directory == "keys":
                ids = [payload]
            else:
                manifest = json.loads(payload)
                if manifest["format"] != 1 or manifest["key"] != file.name:
                    raise CacheError(f"Invalid output manifest for {name}")
                ids = [output["id"] for output in manifest["outputs"].values()]
            for output_id in ids:
                if not ID.fullmatch(output_id):
                    raise CacheError(f"Invalid store ID for {name}")
                archive = f"paths/{output_id}.cpio.zst"
                if not (root / archive).is_file():
                    raise CacheError(f"Missing archive {archive}")
                objects[archive] = root / archive
            objects[name] = file
    if not objects:
        raise CacheError("A generation must contain signed key files")
    return objects


class Directory:
    def __init__(self, root):
        self.root = root

    def sizes(self):
        for directory in ("paths", "keys", "output-keys"):
            if (self.root / directory).is_symlink():
                raise CacheError("Cache destination contains a symlink")
        return {
            path.relative_to(self.root).as_posix(): path.stat().st_size
            for path in self.root.rglob("*") if path.is_file()
        }

    def read(self, name, length):
        with (self.root / name).open("rb") as source:
            return source.read(length)

    def delete(self, name):
        (self.root / name).unlink(missing_ok=True)

    def put(self, name, source):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with source.open("rb") as contents:
                    shutil.copyfileobj(contents, output)
                output.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)


class Bucket:
    def __init__(self):
        import boto3
        from botocore.config import Config

        self.name = os.environ["VUG_CACHE_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ["VUG_CACHE_S3_ENDPOINT"],
            config=Config(s3={"addressing_style": "path"}),
        )

    def sizes(self):
        return {
            item["Key"]: item["Size"]
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.name)
            for item in page.get("Contents", [])
        }

    def read(self, name, length):
        body = self.client.get_object(
            Bucket=self.name, Key=name, Range=f"bytes=0-{length - 1}",
        )["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, name):
        self.client.delete_object(Bucket=self.name, Key=name)

    def put(self, name, source):
        with source.open("rb") as body:
            self.client.put_object(
                Bucket=self.name, Key=name, Body=body, IfNoneMatch="*",
            )


def publish(args):
    desired = {}
    sources = args.generation[:args.keep]
    for root in sources:
        if args.directory and (
            root.resolve().is_relative_to(args.directory.resolve())
            or args.directory.resolve().is_relative_to(root.resolve())
        ):
            raise CacheError("Export and destination directories must be disjoint")
        for name, source in generation(root).items():
            existing = desired.setdefault(name, source)
            if KEY.fullmatch(name) and existing.read_bytes() != source.read_bytes():
                raise CacheError(f"Conflicting retained key {name}")

    target = Directory(args.directory) if args.directory else Bucket()
    sizes = target.sizes()
    stale = sorted(
        (name for name in sizes.keys() - desired.keys()
         if KEY.fullmatch(name) or ARCHIVE.fullmatch(name)),
        key=lambda name: (not KEY.fullmatch(name), name),
    )
    for name, source in desired.items():
        if name not in sizes:
            continue
        size = source.stat().st_size
        if sizes[name] != size or (
            KEY.fullmatch(name) and target.read(name, size) != source.read_bytes()
        ):
            raise CacheError(f"Destination object conflicts with export: {name}")

    for name in stale:
        print(f"Delete {name} ({sizes[name]} bytes)")
    removed = sum(sizes[name] for name in stale)
    pending = {name: source for name, source in desired.items() if name not in sizes}
    incoming = sum(source.stat().st_size for source in pending.values())
    print(f"Total to delete: {removed} bytes")
    if args.apply:
        for name in stale:
            target.delete(name)
        after_prune = target.sizes()
        if after_prune != {name: size for name, size in sizes.items() if name not in stale}:
            raise CacheError("Destination changed during pruning")
        total = sum(after_prune.values()) + incoming
    else:
        total = sum(sizes.values()) - removed + incoming
    print(f"Projected total: {total} bytes (limit: {args.max_bytes} bytes)")
    if total > args.max_bytes:
        raise CacheError("Projected total exceeds the byte limit")
    if args.apply:
        for name in sorted(pending, key=lambda name: (not ARCHIVE.fullmatch(name), name)):
            target.put(name, pending[name])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--directory", type=Path)
    destination.add_argument("--s3", action="store_true")
    parser.add_argument("--generation", type=Path, action="append", required=True,
                        help="complete signed export directory, newest first (repeatable)")
    parser.add_argument("--keep", type=int, default=1)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--apply", action="store_true", help="apply the plan")
    args = parser.parse_args()
    if not 1 <= args.keep <= len(args.generation) or args.max_bytes < 1:
        parser.error("require 1 <= keep <= the number of generations and max-bytes >= 1")
    try:
        publish(args)
    except CacheError as error:
        print(error, file=sys.stderr)
        return 1
    except Exception:
        # SDK and filesystem exceptions may embed endpoint URLs or credentials.
        print("Cache operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

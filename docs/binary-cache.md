# Publishing binary cache generations

Use vug 0.2.13 or later to export a signed cache into a fresh directory.
Stage the indexes against a fixed repository checkout before signing a
public export. The staging script copies matching version indexes, skips
packages excluded from public redistribution, and reads archives from
the original store tree.

```sh
python3 .github/scripts/cache-export-index.py --root . --data-dir data --directory staged
vug store export-cache --data-dir staged --directory current --secret-key-file signing.key
python3 .github/scripts/cache-publish.py --directory published --generation current
python3 .github/scripts/cache-publish.py --directory published --generation current --apply
```

Supply multiple `--generation` directories newest first and use `--keep N`
to retain their union. The default is one generation. Review the deletion
list and projected total before applying. Run one publisher at a time for
each destination and keep the input exports immutable during publication.
The destination must be dedicated to this cache.

Use `--s3` in place of `--directory` with boto3 installed. Set
`VUG_CACHE_S3_ENDPOINT`, `VUG_CACHE_BUCKET`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` in the environment. Set
`AWS_SESSION_TOKEN` when using temporary credentials. Keep the signing
key in the signing process and give the publisher only its signed export.

The publisher projects the size after upload before writing. If the total
would exceed `--max-bytes` (default 9,000,000,000 bytes), it exits.
With pruning enabled, it removes obsolete keys before obsolete archives.
Use `--no-prune` to preserve every existing object and count its size
against the limit. The publisher uploads compressed
archives before signed keys and split-output manifests. A conflicting
existing key causes publication to stop before pruning. Reapply with the
same export to resume an interrupted upload.
Use `--upload-jobs` for concurrent uploads within the archive and metadata phases.
Pass `--replace-archive ID` (repeatable) to overwrite the published archive of
a store ID with the export's copy. Clients accept an archive only when its
unpacked tree hashes to the ID, so replace an archive that fails this check
and nothing else. Export only from a store that sandboxed builds filled:
running a program outside the sandbox can add files such as `__pycache__`
to a store tree, and the archive of that tree no longer hashes to its ID.

## GitHub Actions

The publish workflow `.github/workflows/deploy.yml` runs only by manual
dispatch (`workflow_dispatch`) on master. Pushes and pull requests run checks
and package builds without publishing. The workflow has three jobs:

- `plan` runs in the `dev` environment, which allows deployments from master
  only. It fails unless the publisher configuration is complete and master
  contains the dispatched commit. It selects the packages that differ between
  `base` and the dispatched commit, adds `vug`, and puts packages that depend
  on one another, directly or through other packages, into the same group.
  It packs the groups into at most eight shards by the expected seconds in
  `.github/scripts/build-seconds.json` (30 seconds for an unlisted package),
  heaviest group first.
- `build` runs one job per shard without secrets. Each job unpacks `vug` and
  `vug-bwrap` from the vug release archive of the version in
  `packages/vu/vug`; that `vug` runs its sandboxes with the `vug-bwrap` next
  to it. It builds its packages with the public cache for dependencies,
  stages the index with `cache-export-index.py`, and drops the entries whose key the public cache
  already serves. It exports the remaining entries without signatures and
  uploads their index files and compressed archives as an artifact kept for
  one day.
- `publish` runs in the `dev` environment after every shard succeeds. It
  checks again that master contains the dispatched commit, merges the shard
  artifacts, signs the merged index with `vug store export-cache`, and stops
  unless the number of signed keys equals the number of merged entries. The
  publisher then uses `--keep 2` with the `base` and dispatched checkouts.

vug 0.2.14 or newer resolves reachable keys for each checkout against an
overlay of the signed export and existing cache metadata. The publisher
includes all named outputs of each reachable split manifest. System records
use these same package outputs. The workflow removes the objects that neither
checkout reaches, so the destination stays within the byte limit. Dispatch it
with `dry-run` to print the deletion list and the projected total without
writing, and with `no-prune` to keep every object.

The concurrency group runs one dispatch at a time, and each dispatch builds
the master head at its dispatch time, so publications reach the bucket in
commit order. A run publishes its commit when master contains it, also after
master has moved on: the next dispatch with that commit as `base` keeps both
trees. The `plan` and `publish` jobs fail when master no longer contains the
commit or a later run of the workflow has already succeeded, so a re-run of
an old run cannot prune objects that a newer publication reaches.

The read-only cache URL is `https://pub-da78c0bd9fc84d998fefca158806f160.r2.dev`.
Set the repository variable `VUG_CACHE_PUBLIC_KEY`.
Configure the following secrets in the `dev` environment:

- `VUG_CACHE_SIGNING_KEY`: Ed25519 secret key matching the public key.
- `VUG_CACHE_S3_ENDPOINT`: S3-compatible HTTPS write endpoint followed by the
  bucket name, for example `https://s3.example.test/cache`. The workflow extracts
  `VUG_CACHE_BUCKET` from this value inside the publish step.
- `VUG_CACHE_S3_ACCESS_KEY_ID` and `VUG_CACHE_S3_SECRET_ACCESS_KEY`: bucket credentials.

Dispatch it with `gh workflow run deploy.yml --ref master -f base=<SHA>`.
Set `base` to the last commit whose packages are published: the commit of the
last successful run without `dry-run`. List unchanged packages in `rebuild` to
build them from source as well. A rebuilt package is published only when the
public cache lacks its key. List store IDs in `replace-archives` to overwrite
their published archives; the shards keep the entries of those IDs.

Update `.github/scripts/build-seconds.json` from the per-package seconds in
the job summaries when a package takes a minute or more on the standard
runner.

PR builds use the public URL and public key for dependencies once the key is
configured and the selected vug release supports it. They build changed
packages locally, including changed packages used as dependencies. The
comparison lane uses its own store and builds those packages independently.

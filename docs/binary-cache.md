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

The master push workflow builds changed packages before staging and
signing their outputs in a separate step. It uses `--keep 2` with the
before and after checkouts. vug 0.2.14 or newer resolves reachable keys
for each checkout against an overlay of the signed export and existing
cache metadata. The publisher includes all named outputs of each
reachable split manifest. System records use these same package outputs.
The workflow removes the objects that neither checkout reaches, so the
destination stays within the byte limit. Dispatch it with `dry-run` to print
the deletion list and the projected total without writing, and with `no-prune`
to keep every object.

The read-only cache URL is `https://pub-da78c0bd9fc84d998fefca158806f160.r2.dev`.
Set the repository variable `VUG_CACHE_PUBLIC_KEY`.
Configure the following secrets for the publish workflow:

- `VUG_CACHE_SIGNING_KEY`: Ed25519 secret key matching the public key.
- `VUG_CACHE_S3_ENDPOINT`: S3-compatible HTTPS write endpoint followed by the
  bucket name, for example `https://s3.example.test/cache`. The workflow extracts
  `VUG_CACHE_BUCKET` from this value inside the publish step.
- `VUG_CACHE_S3_ACCESS_KEY_ID` and `VUG_CACHE_S3_SECRET_ACCESS_KEY`: bucket credentials.

Run the workflow manually on master (`workflow_dispatch`) to publish packages
that an earlier push left out. Set `base` to the last commit whose packages
are published; the run builds the packages that differ between `base` and the
master head. List unchanged packages in `rebuild` to build them from source as
well, and list store IDs in `replace-archives` to overwrite their published
archives.

The publish workflow skips its work when configuration is incomplete. PR
builds use the public URL and public key for dependencies once the key is
configured and the selected vug release supports it. They build changed
packages locally, including changed packages used as dependencies. The
comparison lane uses its own store and builds those packages independently.

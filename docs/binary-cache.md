# Publishing binary cache generations

Use vug 0.2.13 or later to export a signed cache into a fresh directory.
Pass an index prepared for the package and system outputs of one fixed
repository commit to `vug store export-cache` using `--data-dir`. The
publisher derives the retained object set from the key files in this
export, including split-output manifests. An export containing accumulated
keys from previous builds is unsuitable as the current generation.

```sh
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

The publisher removes obsolete keys before obsolete archives. After
pruning it counts every object in the destination and projects the size
after upload. If the total would exceed `--max-bytes` (default
9,000,000,000 bytes), it exits before uploading. It uploads compressed
archives before signed keys and split-output manifests. A conflicting
existing key causes publication to stop before pruning. Reapply with the
same export to resume an interrupted upload.

## GitHub Actions

The master push workflow builds changed packages before signing their outputs
in a separate step. It runs the publisher with `--repository` on the fixed
checkout. vug 0.2.14 or newer derives the current generation from the
repository's current package records and an overlay of the signed export
and existing cache metadata. The publisher uses no persisted index. It
retains all named outputs of each reachable split manifest. System
records use these same package outputs.

The read-only cache URL is `https://pub-da78c0bd9fc84d998fefca158806f160.r2.dev`.
Set the repository variable `VUG_CACHE_PUBLIC_KEY`.
Configure the following secrets for the publish workflow:

- `VUG_CACHE_SIGNING_KEY`: Ed25519 secret key matching the public key.
- `VUG_CACHE_S3_ENDPOINT`: S3-compatible HTTPS write endpoint followed by the
  bucket name, for example `https://s3.example.test/cache`. The workflow extracts
  `VUG_CACHE_BUCKET` from this value inside the publish step.
- `VUG_CACHE_S3_ACCESS_KEY_ID` and `VUG_CACHE_S3_SECRET_ACCESS_KEY`: bucket credentials.

The publish workflow skips its work when configuration is incomplete. PR
builds use the public URL and public key for dependencies once the key is
configured and the selected vug release supports it. They build changed
packages locally, including changed packages used as dependencies. The
comparison lane uses its own store and builds those packages independently.

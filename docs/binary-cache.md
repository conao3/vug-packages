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

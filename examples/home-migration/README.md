# Migrate a user environment

This sample maps five Home Manager settings to one Vug home record.

| Home Manager | Vug |
| --- | --- |
| `home.packages` | `packages = ["hello"]` |
| `home.file` | `[[file]]` installs the public settings file |
| `home.sessionVariables` | `[environment]` sets a public variable |
| `systemd.user.services` | `[[unit]]` enables the service at `default.target` |
| `sops.secrets` | `[[secret]]` names a value the service receives through systemd |

Use Vug 0.2.17. Include `home-login` in the system, source
`/etc/profile.d/vug-home.sh` from the system login profile, and provide
SOPS at `/bin/sops`. See [Vug home records][home] for the system and
user credential setup.

Copy `homes/desktop.toml` into the record tree containing `seed.toml` and
`packages/`. As the login user, set up the age identity and source map
under `~/.config/vug/home/secrets/`. Map `example-key` to the encrypted
document using its absolute path and the field `["fixture"]`. Use a
throwaway age identity and sample value when trying the example.

With a trusted cache containing hello and its runtime dependencies, run
these commands from the record tree. Replace the cache URL, public key,
and generation number with the values for your installation.

```sh
vug home build-home --root . --name desktop \
  --binary-cache "$CACHE_URL" --trusted-key "$CACHE_PUBLIC_KEY"
vug home switch-home --generation 1
```

Start a fresh login session. Run `hello`, check that
`VUG_MIGRATION_PROFILE` is `desktop`, and inspect
`~/.config/migration-example/settings.ini`. The service runs hello and
writes the public variable and a digest of the example value under
`$XDG_RUNTIME_DIR`. Use a throwaway value because the sample service
writes its digest.

[home]: https://github.com/conao3/rust-vug/blob/v0.2.17/docs/home.md

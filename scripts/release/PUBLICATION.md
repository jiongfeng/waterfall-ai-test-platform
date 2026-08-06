# Public Release publication gate

`release.yml` creates and remotely re-verifies a Draft Release. The Draft must
never be published manually. `publish-release.yml` is the only authorized
Draft-to-Public path.

The publication job runs behind the `release-publication` environment and:

1. proves the immutable tag resolves to the full revision in the signed
   `RELEASE-MANIFEST.json`;
2. verifies `RELEASE-MANIFEST.json.minisig` with the public key committed at
   that tag, then checks the exact asset set and every SHA-256/size binding;
3. uses an empty Docker authentication directory to pull the exact public GHCR
   digest anonymously;
4. performs the complete online install and lifecycle smoke without registry
   credentials;
5. re-downloads every Draft asset and proves the set is byte-for-byte
   unchanged; and
6. performs `draft=false` as its sole release mutation.

GitHub Container Registry visibility is independent of repository visibility.
The `waterfall-ai-test-platform` package must be changed to Public before the
`release-package-publication` gate is approved. A token-authenticated pull is
not evidence of public availability.

The Minisign secret key is an encrypted-at-rest GitHub Actions secret and must
also be retained in an access-controlled offline backup outside the repository.
Only `scripts/release/minisign.pub` is public. Rotating it requires a reviewed
repository change; previously published releases continue to use the public key
at their own immutable tag.

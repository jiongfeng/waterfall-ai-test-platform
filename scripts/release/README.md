# Release tooling

The release path is split into four signed and protected workflows. A tag
workflow never rebuilds an image that a reviewer approved.

## Signing and GitHub environments

Candidate, approval, and final public manifests are signed with Minisign. The
private key is supplied only as the `MINISIGN_SECRET_KEY` Actions secret; its
public key is `minisign.pub`. This avoids dependence on GitHub artifact
attestations while retaining byte-level bindings across the release chain.

Before starting, an administrator must explicitly create `release-legal`,
`release-package-publication`, and `release-publication` environments and
configure required reviewers;
an automatically created, unprotected environment is not an approval control.

GHCR package visibility is a separate gate from repository visibility. A newly
created container package is private by default, and linking it to a public
repository does not make anonymous pulls available. Before a public release,
an owner must explicitly set the `waterfall-ai-test-platform` container package
to **Public** in GitHub Packages and acknowledge that this visibility change is
not reversible. After the exact digest is promoted, the public workflow pauses
at the preconfigured, required-reviewer `release-package-publication`
environment. While it is waiting, the owner changes that exact package to
Public and only then approves the gate; its clean runner and the later
`online-smoke` both use isolated, empty Docker authentication directories and
must pull anonymously. They fail closed if visibility was not prepared. Only
Enterprise private-repository staging uses a scoped `packages: read` login.

`release.yml` deliberately stops at a re-downloaded, verified Draft Release.
Do not publish that Draft manually. Draft-to-Public is still **NO-GO** until a
separate protected publication workflow re-verifies the immutable tag, remote
Minisign signature and checksums, and performs another anonymous full-bundle
smoke immediately before the sole `draft=false` transition.

1. Run `prepare-release.yml` at the exact full revision and provide its SemVer.
   It tests the source, builds one Linux/amd64 OCI archive, verifies the reviewed
   MySQL 8.4 parent index and amd64 child relationship, generates final-image
   SBOMs, and uploads a private `release-candidate` artifact. It does not push an
   image or create a release.
2. Configure the `release-legal` GitHub environment with required reviewers.
   Run `approve-release.yml` at the same revision, provide the candidate run ID
   and displayed manifest SHA-256, and explicitly select the scopes that the
   evidence supports. The job fails unless GitHub records an approval from that
   protected environment. Its signed approval binds the exact candidate
   manifest, image digest, SBOM hashes, evidence references, and reviewer.
3. Create an immutable `v*` tag at that revision. Run `release.yml` at the tag,
   supplying both run IDs and both displayed manifest hashes. It verifies both
   signatures, promotes the original OCI bytes to GHCR with Skopeo
   `--preserve-digests`, creates bundles, requires an online clean-host smoke,
   and retains a remotely re-verified Draft Release.

The workflow never publishes that Draft. `publish-release.yml` is the sole
authorized publication transition. GHCR package visibility is independent
of repository visibility, and a public container is the only GHCR mode that
allows anonymous pulls. Follow [`PUBLICATION.md`](./PUBLICATION.md): manual
`draft=false` is forbidden; the protected publication workflow must re-verify
the unchanged Draft and pass a credential-free anonymous pull/install smoke.

The offline asset exists only when the approval covers both MySQL offline
redistribution and the MySQL final-image inventory. Its smoke test uses an empty
privileged DinD daemon started with `--network none`; a pull of a pinned, real
Linux/amd64 BusyBox manifest must fail before the bundle is loaded. The
installer then runs as the ordinary runner UID in a new network namespace with
only loopback and no routes. A pending offline decision produces no offline
asset, signed-manifest record, smoke success, or checksum entry.

Every verified bundle includes `INSTALL.md` for the post-extraction setup. It
does not replace the tag's pre-extraction signature procedure: reading files
from an archive before `verify-bundle.sh --extract-to` establishes no trust.

Docker save/load archives do not preserve a registry-assigned RepoDigest. For
that reason, an offline archive carries deterministic local tags of the form
`waterfall-ai-test-platform.local/<component>:sha256-<registry-manifest-hex>`.
The candidate and protected approval separately bind each registry manifest
digest and image config digest. Packaging, verification, and installation use
a storage-aware resolver rather than assuming `docker image inspect .Id` is a
config digest. Classic stores use `.Id`; Docker 29 containerd stores bind
`.Id`/`Descriptor.digest` to the manifest and may expose the real config at the
exact `Descriptor.annotations["config.digest"]` key. The resolver fully streams
`docker image save`, hashes the selected manifest and referenced config JSON,
and cross-checks that annotation when present. Running containers are checked
without a mutable tag: containerd uses `ImageManifestDescriptor.digest`
against the approved manifest, while classic stores use `.Image` against the
approved config. The registry digest remains the source identity and the local
tag is only the offline runtime handle.

The repository files `third-party-images.json` and `final-image-licenses/`
remain fail-closed templates (`false`, `null`, and `NO-GO`). Do not edit them to
make automation pass. Release authorization is a separate, protected, signed
artifact. `release_chain.py` rejects missing hash bindings, altered SBOMs,
untrusted workflow revisions, incomplete decisions, and any `NO-GO` marker in
an approval artifact.

The candidate also exports the exact final platform and MySQL image filesystems
and collects their actual license, licence, COPYING, NOTICE, copyright, legal,
`/usr/share/licenses`, and Debian copyright payloads. Selected links are
resolved only to selected regular files inside the image root; escapes, cycles,
missing targets, oversized payloads, and SPDX-only evidence fail closed. The
protected legal review binds both the SPDX inventories and the collected file
manifests, but automation cannot determine whether upstream notices are legally
complete: the reviewer must compare those payloads with the approved image
sources and redistribution terms before selecting an approval scope.

`assembly/bundle-manifest.json` inside a bundle is only a deterministic digest
map. External trust comes from the Minisign-signed `RELEASE-MANIFEST.json`,
which must be verified before extraction. Candidate and approval Actions artifacts currently
retain for 30 days; copy them to an organization-approved immutable private
archive if a longer review window is required.

# Verify a released box

New Linux releases include cosign keyless signatures for the `.box` and build
manifest. Each signature is stored in `<filename>.bundle`. The certificate
identifies this repository's `release.yml` workflow at the exact version tag.
Signing happens after the guest tests; publication verifies both signatures
again before uploading the draft release.

Install cosign 3.1.4 or newer. Download the box, manifest and their bundles from
the same [GitHub release](https://github.com/cdelgehier/image-factory/releases).
For example, for a signed `v0.7.0` release:

```sh
cosign verify-blob debian-13-arm64.box \
  --bundle debian-13-arm64.box.bundle \
  --certificate-identity 'https://github.com/cdelgehier/image-factory/.github/workflows/release.yml@refs/tags/v0.7.0' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify-blob debian-13-arm64-build-manifest.json \
  --bundle debian-13-arm64-build-manifest.json.bundle \
  --certificate-identity 'https://github.com/cdelgehier/image-factory/.github/workflows/release.yml@refs/tags/v0.7.0' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Use the tag of the downloaded release in both commands. Do not widen the
certificate identity or disable transparency-log verification.

Vagrant still checks the catalogue's SHA-256 during `vagrant up`; it does not
verify cosign bundles itself. Run the commands above when you need to verify
the publisher identity. Existing releases are unchanged and remain unsigned.
Local builds, including the private Windows recipe, are not CI-signed.

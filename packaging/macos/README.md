# macOS packaging

`scripts/build_macos.sh` builds `Archive Builder.app` and `ArchiveBuilderCLI` as Universal2 binaries on the Intel GitHub macOS runner using a Universal2 Python installation.

The deployment target is macOS 12.0 (Monterey). The output is `ArchiveBuilder-macOS-Universal.zip` with a SHA-256 checksum.

The build writes the source release version into both `CFBundleShortVersionString` and `CFBundleVersion` before the final ad-hoc signature. The bundle identifier is `org.memory-of-time.archive-builder`.

The script uses `ditto` so macOS bundle metadata and symbolic links survive packaging, then extracts and verifies the finished ZIP before GitHub Actions uploads it. Builds are ad-hoc code signed; Apple notarization is not configured by default.

# Automatic updates

Project Master uses Tauri's signed desktop updater. The v0.3.0 beta candidate checks the configured
rolling beta channel in the background eight seconds after startup, but records each attempt before
making the network request so an offline computer is not queried again on every launch.

> **Beta channel not yet provisioned:** the desktop is configured for `updater-beta`, but the
> repository currently contains only the historical Windows alpha publishing workflow. Until a
> signed, version-matched beta channel is created and tested, v0.3.0 must be installed and upgraded
> manually. Do not point the beta candidate back at the alpha channel.

## Bootstrap requirement

The public v0.2.1 build does not contain the updater. v0.2.2 contains the updater but watches
`updater-alpha`; it will not discover a release published only to `updater-beta`. Existing alpha
users must therefore install the first v0.3.0 beta manually unless a reviewed migration notice is
published through the alpha channel.

Linux updater artifacts are not currently published at all. The first Fedora beta remains a manual
RPM/AppImage installation until the beta workflow builds, signs, and tests Linux update artifacts.

## Check cadence

- Alpha builds check at most once every 24 hours.
- Beta and stable builds check at most once every seven days.
- The release stage is explicit in `src/lib/updatePolicy.ts`; Project Master never guesses maturity
  from commit activity or elapsed time.

`CURRENT_RELEASE_STAGE` and the endpoint in `src-tauri/tauri.conf.json` must always name the same
release channel. Moving to stable later requires changing and testing both in the same release.

## Security model

Update packages are signed. Tauri verifies each package against the public key embedded in
`src-tauri/tauri.conf.json` before installation. The private key must never be committed.

The canonical local private key is stored at:

`C:\Users\RealM\.tauri\project-master-updater.key`

Its password is stored locally in `C:\Users\RealM\.tauri\project-master-updater.password.dpapi`,
encrypted with Windows DPAPI for the current Windows account. The GitHub repository must store the
key and password as the `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` Actions
secrets before the publishing workflow can run. Back up both in a secure credential vault: losing
either prevents existing installations from trusting future updates.

## Historical alpha publishing path

The existing `Publish signed alpha update` workflow accepts tags such as `v0.2.2-alpha`, builds and
signs the Windows installer, keeps the GitHub Release marked as a prerelease, and replaces
`latest.json` on the `updater-alpha` channel. It is retained for historical alpha maintenance and
does not publish the v0.3.0 beta.

Before enabling beta updates, add a separately reviewed workflow that requires the `0.3.0 BETA RC`
changelog contract, builds the intended operating-system artifacts, verifies their signatures and
checksums, publishes only from an explicit beta tag, and maintains `updater-beta/latest.json`.

The updater only checks and prompts automatically. Download, installation, and restart require the
user to choose **Update and restart**. It will not interrupt an active model response.

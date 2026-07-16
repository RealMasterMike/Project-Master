# Automatic updates

Project Master uses Tauri's signed desktop updater. The application checks the rolling alpha
channel in the background eight seconds after startup, but it records each attempt before making
the network request so an offline computer is not queried again on every launch.

## Bootstrap requirement

The public v0.2.1 build does not contain the updater. Existing users must manually download and
install the latest Project Master release from GitHub once to receive the updater. Automatic checks
and in-app installation are available only after that first updater-enabled version is installed.

## Check cadence

- Alpha builds check at most once every 24 hours.
- Beta and stable builds check at most once every seven days.
- The release stage is explicit in `src/lib/updatePolicy.ts`; Project Master never guesses maturity
  from commit activity or elapsed time.

When the project is ready to move beyond alpha, change `CURRENT_RELEASE_STAGE` and move the updater
endpoint in `src-tauri/tauri.conf.json` to the matching release channel as part of the same release.

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

## Publishing an alpha update

1. Update every Project Master version source and add the versioned changelog entry.
2. Merge the tested change to the release commit.
3. Push a matching tag such as `v0.2.2-alpha`.
4. The `Publish signed alpha update` workflow builds and signs the Windows installer, keeps the
   GitHub Release marked as a prerelease, and replaces `latest.json` on the `updater-alpha` channel.

The updater only checks and prompts automatically. Download, installation, and restart require the
user to choose **Update and restart**. It will not interrupt an active model response.

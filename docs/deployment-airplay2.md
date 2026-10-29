# Experimental AirPlay 2 bridge

This opt-in receiver uses Shairport Sync 5.5.2 and NQPTP 1.2.8 to feed 48 kHz, S16LE, stereo PCM into an HTTP WAV stream pulled by one selected Xiaomi speaker. Other speakers retain AirPlay 1; DLNA remains available.

Shairport handles pairing, buffered audio, and receiver-side PTP timing. A small stdout extension preserves planned playback times and session boundaries. The HTTP bridge schedules audio against those timestamps using Linux `CLOCK_MONOTONIC_RAW`.

Xiaomi's HTTP player exposes neither its DAC clock nor buffer depth. **Acoustic synchronization with HomePod or other receivers has not been verified.** Output latency is a manual estimate, not hardware clock feedback.

## Install or update

The standard `mrdeer1997/miair-next:latest` image includes Shairport Sync and NQPTP on both amd64 and arm64. Use the existing [installation instructions](deployment.md), including the one-command installer, standard Compose, fnOS, or iStore deployment. No separate AirPlay 2 image or source build is required. Use a Linux host with host networking; Docker Desktop on Windows/macOS cannot provide the required LAN multicast path.

For an existing installation, update through its normal container management flow. With `manage.sh`:

```sh
./manage.sh update
```

For the standard Compose deployment:

```sh
docker compose pull
docker compose up -d
```

Keep the existing data mount. Updates preserve accounts, enabled speakers and protocol settings. AirPlay 1 remains the default until AirPlay 2 is enabled in service settings. A successful main-branch image workflow updates `latest`; simply merging code does not update an already-running container. Version tags also publish versioned images and a GitHub Release. The panel's release checker detects those releases, not every main-branch image.

Allow the Web and DLNA ports (8300/8200 by default), AirPlay TCP 7000, mDNS UDP 5353, dynamically allocated HTTP audio ports, and PTP UDP 319/320. NQPTP also uses local UDP 9000. Conflicting PTP services must be stopped first. Keep the container's private IPC namespace; do not mount the host's `/run` or use `--ipc=host`.

The standard image starts discovery daemons before delegating to the existing privilege-drop entrypoint. The backend, Shairport Sync, and NQPTP run as `miair`; NQPTP receives only the file capability needed to bind low-numbered PTP ports. Avahi uses its own default privilege drop. No privileged-container mode or extra Docker capability is required.

On hosts with multiple interfaces, set `MIAIR_AIRPLAY_INTERFACE` to the LAN interface reported by `ip link` (for example `br-lan`). The backend's advertised address must remain reachable from the speaker.

### Optional source-build testing

The default Docker build includes both receiver implementations:

```sh
docker build -t miair-next:local .
```

To test with an isolated data directory, stop any existing MiAir container first, preserve its data, and set the reachable LAN address:

```sh
export MIAIR_HOSTNAME=<host-LAN-IP>
docker compose -f docker-compose.airplay2.yml up -d --build
```

This development Compose file retains the `airplay2` build target and separate `data-airplay2-test` directory. It is optional; normal installations use the standard image and their existing data directory.

## Settings and calibration

Open the Web UI at the configured host and port. Configure a Xiaomi account, enable speakers, then turn on **为一个音箱启用 AirPlay 2（实验性）** under service settings and choose one target speaker. Other enabled speakers continue to use AirPlay 1. Save to restart the services. AirPlay settings use Simplified Chinese, following the upstream interface. `MIAIR_AIRPLAY_MODE` supplies a default for new configurations; saved settings take precedence.

The output latency estimate accepts 0–4000 ms. Increasing it sends audio earlier; decreasing it sends audio later. The default 1000 ms is a nominal estimate, not a measurement. Apply a latency change and reconnect the sender before comparing against another receiver. The latency-only save preserves other unsaved form fields. Input precision of 1 ms does not imply acoustic accuracy of 1 ms.

Buffered audio must arrive far enough ahead to meet the requested lead. When it does not, the bridge retains unexpired audio and reports the shortfall. A larger setting cannot compensate for audio not yet received or varying firmware buffers. Startup may skip expired audio, including a small part of a track's beginning.

## Behavior and limits

- One AirPlay 2 receiver per host IP; other enabled speakers use AirPlay 1. A sender may also select receivers on other IPs.
- PCM is software-volume controlled by Shairport. Hardware volume is not synchronized bidirectionally.
- Sender-provided track information and JPEG/PNG artwork appear in the dashboard. Metadata uses Shairport's separate FIFO, leaving timed PCM unchanged. Artwork is held in memory (up to 4 MiB), requires panel authentication, and expires when replaced or disconnected. Apps that omit metadata cannot supply these details.
- Enable **Touchscreen lyrics** in display settings to match AirPlay 2 titles/artists against Xiaomi's catalog asynchronously. Supported touchscreen speakers obtain catalog lyrics and cover art via the matched audioID; this does not send the sender's image or lyric text to the Xiaomi screen. An unavailable match falls back to the configured default audioID. Matching and track changes may briefly rebuffer the HTTP player, as with the existing AirPlay 1 lyrics path.
- Home App provisioning and video are unavailable.
- Pause, immediate flush, and stop retire the old WAV URL and connection. Resume creates a new URL and reissues the Xiaomi play command, which can briefly rebuffer. Deferred flush does not immediately stop playback.
- Control boundaries use level 2 messages from the pinned Shairport version, plus in-band stdout epochs. Revalidate both when upgrading it. The adapter must not enable `MIAIR_AIRPLAY2_TIMED_STDOUT` with an unpatched binary.
- Each WAV session has one consumer. Old URLs return 410 while another session is active; HEAD does not consume PCM. The bridge adds no synthetic silence during gaps.
- Timestamp discontinuities, delivery stalls, and HTTP failures may rebuild the downstream stream. Recovery is rate-limited to once per three seconds. This detects server-side timing faults, not pauses confined to the speaker's internal player.
- Startup failure falls back to AirPlay 1 and exposes the error. Runtime exit stops both native processes and the HTTP bridge; save settings or restart to retry. A Web health check alone does not establish AirPlay 2 readiness.

## Verification and rollback

Test discovery, initial playback, software volume/mute, pause/resume, track changes, repeated seeks, disconnect/reconnect, and container restart on the intended sender and speaker. Compare initial and continuous acoustic output against another receiver, including after seeks; simultaneous selection alone does not establish synchronization.

```sh
docker logs --tail 200 miair-next
docker exec miair-next shairport-sync -V
docker exec miair-next nqptp -V
```

Enable debug application logging for native process details. Preserve and redact logs before sharing. The status page reports process liveness and startup/runtime errors, not measured acoustic alignment.

AirPlay 2 audio diagnostics expose the HTTP bridge's client count, PCM queue depth and sample format. Native Shairport RTP/decoder counters are unavailable through this adapter and are omitted; the dashboard only shows metrics supplied by the active receiver.

To return all speakers to AirPlay 1, turn off **为一个音箱启用 AirPlay 2（实验性）** and save. For isolated source-build testing, stop the test container before restarting the previous installation. Neither action requires deleting data.

References: [Shairport Sync AirPlay 2](https://github.com/mikebrady/shairport-sync/blob/5.5.2/AIRPLAY2.md), [Shairport Sync 5.5.2](https://github.com/mikebrady/shairport-sync/releases/tag/5.5.2), [NQPTP 1.2.8](https://github.com/mikebrady/nqptp/tree/1.2.8).

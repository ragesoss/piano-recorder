# Plan: a practice recorder (started 2026-10-10)

## Goal

Record practice and play it back over the room's speakers, run from a
phone. Use the gear already at hand:

- the BMG-04F mixer, with the two small-diaphragm condensers into it and
  the speakers on its output;
- a small headless computer on the mixer's USB as the control unit;
- a web page on the phone that talks to the control unit: start and stop a
  recording, list the takes, play one over the speakers.

What each part is, what is known about it, and how to swap it:
`docs/components.md`.

## Approach

- **What's at hand first, upgrades later.** Get the whole system working
  with the gear at hand. Then upgrade one part at a time, where using it
  shows a need. A better mixer or interface is likely at some point, but
  not before the system works end to end (the player, 2026-10-10).
- **Modular.** Each part fills a slot with a written interface, its tests
  and its swap steps (`docs/components.md`). The software reaches hardware
  only through profiles and one config file. A swap is then:
  - a profile;
  - a config change;
  - the slot's tests;
  - a line in the swap log.

## Design for the first version

### Hardware layer

- **Profiles:** `hardware/<part>.toml`, one per audio device, written from
  a probe of the real device. A profile holds:
  - the USB id and ALSA card id;
  - for capture and playback: format, rate, channels and channel layout
    (for example "mono, duplicated: keep channel 0");
  - whether to remove DC offset;
  - the ALSA control names and ranges for capture gain and playback volume,
    with their set values.
- **Config:** one file on the control unit, for example
  `/etc/practice-recorder.toml`:
  - `capture = "bmg-04f"`, `playback = "bmg-04f"` (profile names; they
    may differ);
  - `takes_dir`, `address`, the web port.
- **One audio module** reads the profiles and opens the devices. Nothing
  else in the code names a card, a control or a format. The stored take
  format follows the capture profile. Playback converts if the playback
  device needs another format.
- **`tools/hwcheck`** runs the automated tests (T1-T4, T7, T9, T10 in
  `docs/components.md`) against the config or a named profile. It prints
  a short report for the swap log.
- **Setup script:** one script turns a fresh OS into a control unit:
  packages, user, the systemd unit, the deploy. Swapping the control unit is
  then: install the OS, run it, copy the config, run the tests.

### Control unit

- **Raspberry Pi 3,** with the LIVA as the alternate (slot 5 in
  `docs/components.md`). The Pi is powerful enough. Its risk is USB, which
  the soak test (T9) checks.
- Raspberry Pi OS Lite, 64-bit, headless. ALSA directly, with no
  PulseAudio or PipeWire. The service opens the `hw:` devices itself.
- Later features that process audio (slowing a take down without changing
  its pitch) are the only heavy work. `sox tempo` is light; rubberband is
  heavier. Measure on the Pi 3 when that feature comes up.

### Service

One small Python web app (FastAPI and uvicorn in a venv), run by systemd
at boot.

- **Recording:** `arecord` on the capture device, raw, piped into the
  service. The service applies the profile's channel layout and DC removal,
  writes a WAV, and computes a live peak level for the page.
- **Playback:** the service reads the take and writes raw PCM into `aplay`
  on the playback device. This handles seeking, looping and gain in Python.
  Stop means kill.
  - **Loudness:** each take is brought to a target gated rms (about
    -21 dBFS, so takes match records at the shared knob setting), through a
    peak limiter at -1 dBFS. The target is a config value, calibrated
    against a record ("Shared mixer" in `docs/components.md`).
- **Volume:** `amixer` on the playback profile's volume control.
- It survives a device being switched off or unplugged: it reports "mixer
  not found" and finds the device again by its card id.

### Phone page

Served by the control unit, saved to the home screen:

- one big Record/Stop button, the elapsed time, a level meter with a clip
  warning;
- the takes, newest first (time and length): play on the speakers, stop,
  seek, play on the phone itself, delete, add a note;
- a speaker volume slider; mixer connected or not.

Home network only, no login, for the first version.

## Steps

### 0. This plan and the component inventory (done 2026-10-10)

### 1. The control unit on the network (done 2026-10-10)

Both boxes have sat unplugged for a long time, so the OS is installed
fresh (the player, 2026-10-10).

1. **Write the card, headless** (done 2026-10-10): Raspberry Pi OS Lite
   (64-bit), with cloud-init files for the hostname `practice`, the user
   and the SSH key. Wi-Fi was then set up over SSH, through Ethernet:
   on this image, Wi-Fi from cloud-init does not survive the first boot.
   How, and the files: `setup/rpi/`. No Raspberry Pi Imager, monitor or
   keyboard.
   - Power the Pi 3 from a proper 5 V, 2.5 A supply (see slot 5). Nothing
     in its USB ports: the Edimax Wi-Fi adapter is retired.
2. Boot it and find it (done 2026-10-10): on Wi-Fi, at an address from
   DHCP.
3. Reserve that address on the router for the Pi's Wi-Fi MAC address
   (done). `Host practice` is in `~/.ssh/config` (done); the Pi's old
   entry there can go. The address, the MAC address and the entries:
   `local/README.md` (not in git).
4. From here: `ssh practice`, then `sudo apt update && sudo apt full-upgrade`,
   then `sudo apt install alsa-utils flac python3-venv` (done).
5. Record what was set up in slots 5-7 of `docs/components.md`.
6. **Done when:** `ssh practice` works with the key and no password, and
   after a reboot it comes back at the same address (T10).

If the Pi 3 fails the soak test (T9) in step 2, the LIVA takes its place:
Debian (netinst, no desktop, SSH server) from a USB stick onto its eMMC.
That install needs a keyboard and monitor on the LIVA once. After that,
the same SSH, address and package steps apply.

### 2. The mixer on the control unit

The player has to be in the room. This is the first run of slot 2 and
slot 3's tests on the control unit. The test ids are in
`docs/components.md`.

1. Connect the mixer's USB-A socket to a Pi USB-A port, with the A-to-A
   cable and adapter that worked on the laptop. Run T1-T3 by hand. The
   results should match the laptop's.
2. **How the mixer is powered:** its own adapter, or USB? (See "USB power"
   under Interactions.)
3. **The panel:** photos of the front and back. Which output do the
   speakers use? Is there a separate monitor, AUX or headphone output with
   its own level? Is there a USB/Bluetooth level knob? What are the
   speakers (powered? their own volume knob? other inputs?)
4. T4 (floor), then T5 (level) with the instrument.
5. T6 (playback): does USB playback come out of the speakers? Which knobs
   change it? Does `PCM Playback Volume`?
6. T7 (loopback) and T8 (bleed). If T8 fails, work through "Speakers while
   recording" under Interactions.
7. T9 (soak), 30 minutes.
8. Fill in slots 2-4 and the swap log in `docs/components.md`.
9. **Done when:** a take recorded on the Pi plays back over the speakers,
   and nothing in the setup needs a walk to the speakers between takes.

### 3. The service and the phone page (first version)

1. **The hardware layer first:**
   - the BMG-04F profile, from step 2's probe;
   - the config;
   - the audio module;
   - `tools/hwcheck` (re-run step 2's auto tests with it);
   - the setup script.
2. Then the service and the page. Develop on this desktop against a fake
   device profile, then deploy to the control unit.
3. **Done when:** T11 passes. From the phone, a take is started, stopped,
   listed and played over the speakers. No laptop, and no walk to the
   speakers.

### 4. Practice use, then later features

Use it for a week or so before adding anything. Candidates:

- **Retroactive record:** always capture into a ring buffer, and a "keep
  the last N minutes" button. A good take is then never missed.
- loop a section; slow down without changing pitch (`sox tempo` or
  rubberband);
- compare two takes A/B;
- a click or a drone through the speakers (it needs T7's answer);
- copy takes to the desktop or Dropbox automatically (rsync or rclone);
- for dobro takes, a hook into the dobro-transcription pipeline.

Upgrades follow the same rule: one slot at a time, by the swap steps in
`docs/components.md`.

- **Decided 2026-10-10: a new interface for slots 2 and 3**, because of
  what step 2 found: the mics in the speakers, mono 16-bit capture, and the
  shared channel 3/4. The leading option is a Scarlett 2i2 (a used 3rd
  Gen, the player is looking) for the mics and the Pi. Its output feeds a
  BMG-04F channel, and the BMG-04F stays as the room's mixer for records
  and the speakers. The speakers take one source each (JBL manual). Requirements and the candidate:
  `docs/replacing-the-mixer.md`. Building on the BMG-04F continues until
  then. The swap is a new profile plus the tests.

## Open questions

- ~~Which instrument?~~ The piano (2026-10-10). The speakers are across
  the room, with the piano between. Mic placement at the piano is still
  open. The mixer's USB is mono, so the pair of SDCs gives no stereo
  through it.
- Is there Ethernet in the mixer's room, or Wi-Fi only?
- What are the speakers, and how are they connected?
- Is there a spare USB DAC or USB sound card anywhere (for option B under
  "Speakers while recording")?

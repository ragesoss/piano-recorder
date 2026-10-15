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

**2026-10-14/15: the LIVA took the Pi's place** (slot 5, swap log), after
the Pi's T6 popped with the 18i8. It was upgraded in place over SSH, not
installed fresh, so no keyboard or monitor was needed. It is `practice` now,
on 5 GHz Wi-Fi at a new reserved address (`local/README.md`). The Pi is off.
- **The Pi lost capture samples with the 18i8:** all 11 takes it recorded
  through the 18i8 (2026-10-14, 19:26-21:44, bass exercises and Down
  Yonder) have 4-17 gaps each. They are worth recording again on the LIVA.
  Its T6 popping was most likely in the take it played.
- **The LIVA:** T6, T9 (sample-exact, in duplex: no capture samples lost)
  and T10 pass. With `snd_usb_audio lowlatency=0`, about one 1 ms playback
  dropout every 2 hours remains.
- **Due:** T1-T4 and T11 on the LIVA; the line-up's time after a take.

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

### 5. Play-along takes (started 2026-10-12; moved to the exercise app, step 6)

The player's request: record new instruments along with recordings from
`~/play/dobro-transcription`, for transcribing every instrument of a band
at once. Claude puts a recording on the control unit; the player picks it
on the phone, presses Record along, and plays with it over the speakers.

- **First use:** a bass, with the Nadine pickup on its afterlength in
  place of one condenser (channel 1 or 2), the other condenser off (each
  channel has its own +48V button). A contact pickup hears the strings,
  not the speakers, so the speakers can play the backing.
- **Backing tracks:** `tools/push-backing` converts a recording to 48 kHz
  mono and copies it, with a sidecar saying where it came from, into the
  config's `backing_dir` (`~/backing`).
- **Recording:** the capture starts, then the backing plays at the takes'
  loudness; the take ends 2 s after the backing, or at Stop.
- **Lining up:** the BMG-04F records its own playback (T7), so the backing
  is in every play-along take electrically. After the take, the service
  finds it by cross-correlation, to the sample, fits a 64-tap filter to
  its path, and saves the take again without it (`<take>.clean.wav`). The
  sidecar keeps where the backing starts (`lag_samples`) and how much of it
  is left. Through the air the path is long and changing, and only its
  first milliseconds come out; with a contact pickup that is little.
  - On the desktop (fake device, its loopback at -31 dB and 9 ms): the
    backing is found to the sample and taken out by more than 25 dB, and
    on synthetic data to the noise floor.
- **Getting takes:** `tools/pull-take TAKE_ID DEST_DIR` copies the take, its
  sidecar and the copy without the backing.
- **First takes on the control unit (2026-10-12, evening):** two takes of
  bass with the Nadine along with Down Yonder take 1 (Beard, click 120),
  then a bass-only take to check the level.
  - **Lined up:** +51.9 ms and -73.2 ms (the second backing began before
    the capture's first sample: the backing now starts once the first
    capture block has arrived, and the search covers -2 s to +10 s).
  - **The backing in the takes:** about -41 dBFS before the bass came in,
    partly through the air (the speakers into the bass body). A filter
    fitted to its path takes out little of it (5-7 dB even fitted to the
    seconds before the bass): play-along takes keep much of the backing.
  - **Crackles: the Pi 3's USB in duplex.** The second take lost 29 pieces
    of its playback ("Transfer to device 4 endpoint 0x3 failed - FIQ
    reported NYET. Data may have been lost"), 38 clicks in the take; the
    first take was clean of USB errors, 3 clicks near its end. The bass-only
    take (no playback): no clicks. The second take started while the
    service was still lining up the first: load, as in the first T9
    failure (slot 5).
  - **Level:** the Nadine at evening loudness, full attacks, peaked at
    -38.1 dBFS (loudest 50 ms -48, floor -81). Its channel's GAIN needs
    about +28 dB. Not calibrated yet.
  - The duplex soak (T9 `--duplex`) was started from the desktop during
    the player's session: it held the capture device and the player's
    Record failed. Device tests run only when the player says so.
- **Nadine level (2026-10-13 morning):** on channel 2, its GAIN at maximum:
  peaks -25 to -31 dBFS, the loudest 50 ms -39 to -43, the floor -82
  (mixer noise; the 16-bit floor is near -98), so about 39 dB from notes to
  floor and no clipping. Clean enough for transcription. Channel 2's LEVEL
  knob would raise the meter and playback, not the ratio; left as is (the
  player: enough knobs for now). Playback boosts at most 16 dB, so these
  takes play back quieter than others.
- **Next:** the line-up after a take in a separate, low-priority process,
  never during a recording; then play-along takes again, short. If they
  still lose data, the LIVA (slot 5's alternate: an Intel USB controller)
  or the Scarlett (high-speed USB, no split transactions).
- **With a new interface:** the Scarlett 18i8 3rd Gen's routing matrix can
  send any source, its playback included, to any capture channel
  (scarlett2 driver; alsa-scarlett-gui), so the backing can come back
  electrically on a channel of its own instead of in the mix (from a
  survey, 2026-10-13; to check on the device). Without that routing the
  offset has to come from the air path (the speakers into the mics) or
  from a latency measured once per device.

### 6. The exercise app (started 2026-10-13)

The player's request: record exercise takes for `~/play/dobro-transcription`
from a separate app, not the practice recorder, which is for the piano and
its player only. Tablet-first (an iPad, held landscape), with the exercise
on the same screen as the controls, so a take needs little preparation.

- **A third program on the control unit:** `exercises/`, port 8002,
  `setup/exercise-recorder.service`, its own config
  (`/etc/exercise-recorder.toml` from `config/exercises.toml`), takes
  (`~/exercise-takes`) and exercises (`~/exercises`). It uses the
  recorder's engine, devices, loudness and line-up (`recorder/`), and takes
  turns with the others for the speakers (`peers`: 8000 and 8001; the
  practice recorder and the ambient synth list 8002).
- **Exercises:** `tools/push-exercise SCORE.musicxml` renders the score's
  pages with MuseScore on the desktop (landscape, larger staves, trimmed:
  `tools/exercise-page.mss`), reads its tempo, meter, pickup and length
  (repeats played out), and copies the pages, the score and
  `exercise.json` to the control unit.
- **The page:** the score on the left; on the right the exercise, its
  tempo (4 bpm steps, kept per exercise), Count-in and Click switches, a
  backing track instead of the click, Record, the count-in counted down and
  then the bar and beat, the meter, and the exercise's takes.
- **A take over the click** (`Engine.start_click`,
  `playalong.click_track`): a bar's count-in before the bar the music
  starts in, the pickup bar's beats, then every beat to the end. The click
  is found in the take like a backing (the mixer returns it), and the take
  records where the score's beat 0 is: `beat0_s` for the exercise's
  `takes.csv` in dobro-transcription. Without Click, only the count-in
  plays.
- **Play-along takes moved here** from the practice recorder (2026-10-13:
  its page has no Play along card, its API no `/api/playalong/start`);
  takes made there before still line up and play without their backing.
- **Tried on the desktop** (fake device): exercise 005 at 72 bpm, the
  count-in counted down, then "Bar 1 · 3"; the take lined up with its
  click (peak ratio 87), beat 0 at 3.644 s.
- **Deployed 2026-10-13.** The Pi's `/etc/practice-recorder.toml`,
  `/etc/ambient-room.toml` (peers) and `/etc/room-home.toml` (the tile)
  were edited by hand to add it (`.bak` copies kept). The eight bass takes
  of 2026-10-12/13 (four play-alongs, three gain checks, the noodles) moved
  from the practice recorder's takes to the exercise app's. On the Pi: Down
  Yonder bass and ten bass exercises (dobro-transcription
  `exercises/bass/`).
- **First exercise takes (2026-10-13, about 15:59-16:06):** bass
  exercises 001-006 over the click, brought into dobro-transcription by
  hand. The line-up took 23-27 s per take on the Pi 3 (the whole-take
  search and two path fits for the copy without the click), and put four of
  the six 8-12 beats late: a click track correlates with itself shifted by
  a bar (or a beat) almost as well as unshifted, and the noise decides. The
  clicks all arrived 160-190 ms into the takes.
- **Lining up a click since then** (`playalong.find_click`): searched only
  in the take's first 0.4 s, or one beat if that is shorter, so the beats
  either side of the true one fall outside the search, over the click's
  first 8 s. On the six takes: the right lag to within 1 ms, peaks 73-215
  times the median (noise alone about 6); 1.5 s on the Pi 3 (35 ms on the
  desktop). Deployed 2026-10-13, about 16:20. No copy
  without the click: nothing on the control unit uses one, and the click
  is out of the bass's band; one can be made on the desktop. Play-along
  takes over a backing are lined up and cleaned as before (slowly).
- **Choosing an exercise, and what it still wants** (2026-10-14, the
  player's request): the dropdown became a card (instrument and number,
  title, status, and why a new take is wanted) that opens a picker: a tab
  per instrument (with a count of what needs a take or a review), filters
  (all, needs a take, to review, good), and a row per exercise with its
  status badge, tempo, bars and last take. The status comes from
  dobro-transcription (`exercises/status.py --json | tools/push-status`:
  `status.json` in exercises_dir, read on every listing); the app adds
  what only it knows, the takes made here that the project does not have
  yet ("to review": newer than the newest take id the project lists). A
  "Played on" choice under the card (the instruments the project keeps
  notes on, of the exercise's kind, or another), remembered per kind and
  written to each take's sidecar (`instrument_id`, `instrument_name`).
  Deployed 2026-10-14 with 60 exercises: bass, dobro, guitar, banjo.
- **Next:** a tool to bring exercise takes into dobro-transcription with
  their `takes.csv` rows (`tools/pull-take` reads only the practice
  recorder's takes; exercise takes need `/etc/exercise-recorder.toml`),
  which could also take the backing out on the desktop instead of the Pi.

## Open questions

- ~~Which instrument?~~ The piano (2026-10-10). The speakers are across
  the room, with the piano between. Mic placement at the piano is still
  open. The mixer's USB is mono, so the pair of SDCs gives no stereo
  through it.
- Is there Ethernet in the mixer's room, or Wi-Fi only?
- What are the speakers, and how are they connected?
- Is there a spare USB DAC or USB sound card anywhere (for option B under
  "Speakers while recording")?

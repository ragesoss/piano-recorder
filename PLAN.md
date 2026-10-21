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

- **Restyled 2026-10-16,** with the home page, in the exercise app's new look
  (step 6, "The page, redesigned"): dark, purple for what to tap, red only
  for recording, and its fonts (each server has `/fonts/`, from
  `exercises/static/fonts/`). Every feature is kept.
  - On a phone, a take that is playing sits at the foot of the screen, with
    Stop and where it is (tap to play from there). On an iPad, landscape or
    portrait, recording is on the left and the takes scroll on the right.
  - A take that is playing has Stop in place of Speakers. The speakers'
    level has − and + buttons (1 dB). A server error can be put away (OK)
    until a different one comes.
  - The home page's tiles say what each app is doing in words: the exercise
    app's like the practice recorder's (it showed the raw state before), and
    the chords app's to come (port 8003): "Hearing G", "Listening",
    "Letting a recorder have the inputs", "Ready". A recording turns its
    tile red.
  - Tried on the desktop (fake device, headless Chromium at 390x844,
    1180x820 and 820x1180): takes recorded, played, sought, noted and
    deleted; the level changed; "Stop everything" stopped a recording. No
    console errors, and no sideways scroll on the phone. Not yet deployed.
  - The BMG-04F's note ("the mixer's channel 3/4 button must be on MP3/USB"
    to hear takes) is gone from both pages (2026-10-16, the player): takes
    play through the 18i8 since 2026-10-14.

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
  never during a recording; then play-along takes again, short.
- **2026-10-16, evening: three-minute play-along takes ran the LIVA out of memory.** The line-up
  transformed the whole take in double precision (2.0 GB at the peak for a 188 s take over Saint
  Anne's Reel, on 1.86 GB): that take's line-up never finished, the app held 1.45 GB idle after it,
  and the next take froze at 28 s with an xrun. Now the lag search runs in single precision and the
  path's correlations are summed a block at a time: the same lag, levels and cleaned take, 0.68 GB at
  the peak. Deployed the same evening. (The 18:31 take's sidecar still has no line-up: "line up
  again" would fill it in.) If they
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
  `exercise.json` to the control unit (since 2026-10-16 also its notes as
  audio, `chart.wav`: below).
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
- **The instructions with the score** (2026-10-15, the player's request):
  `tools/push-exercise` sends the exercise's README.md (beside the score;
  `--readme`, or `--no-readme` for the tunes, whose READMEs are about
  their recordings) as `readme.md`; the page shows it above the score in a
  card that folds away (Instructions; open or folded remembered on the
  iPad), from `/api/exercises/<id>/readme`. The page reads the Markdown
  itself (headings, paragraphs, lists, tables, code; all text escaped,
  links as their text). Tried on the desktop with dobro 005 (its ASCII
  tab) and bass 011 (its table of bars) at 1180x820. Deployed 2026-10-15
  (just before 14:00, everything idle), and the 59 exercises pushed again with
  their instructions; the tunes have none.
- **Lining up a click through the 18i8** (2026-10-15): with the 18i8 the click is not
  returned electrically; it reaches the room microphone (and faintly the bass's pickup) through
  the air 300-420 ms after the track, and the phase-transform correlation (which weighs every
  frequency alike, where the click is two narrow tones in a take that is mostly bass) put 13 of
  14 takes at 57-200 ms, eight of them marked found. `find_click` now finds the click by its
  onsets in its own bands (1.5 and 2 kHz, every millisecond, every channel), within 0.6 s or one
  beat, then to the sample by the phase transform within 2 ms of that; found when the take has
  an onset where 70% of the click's beats fall (within 15 ms) and the onsets' peak is 3 times
  its median. On the takes of 2026-10-13 to 15 (15 through the 18i8, 11 through the BMG-04F):
  all found, 24 at the lag measured by hand (one BMG-04F take 28 ms off, one 18i8 take 13 ms);
  a click at the wrong tempo never found. Not yet deployed.
- **The start panel** (2026-10-15, the player's feedback: "the ui for starting a recording is
  bewildering"; a backing pushed that day could not be found, and a guitar take went in as
  "another instrument (not listed)"). The controls above Record now say what the next take is:
  - **What:** a switch, Exercise or Free take, in one tap (the last exercise comes back); the
    card under it opens the picker, which no longer has a Free take button.
  - **From the speakers:** a switch, the click (Nothing, for a free take) or a backing track
    (with how many there are), and a list of the backings when that is chosen. The backings
    are listed again every 8 s while the page is idle and in view, and when the switch is
    touched, so one pushed while the page is open shows up (and is the one offered next)
    without a reload. Remembered apart for exercises and free takes.
  - **Played on:** every known instrument, the exercise's kind first, and for each kind one
    not listed ("Another guitar (not listed)", "A banjo (not listed)"), then "Something else".
    The sidecar's `instrument_name` for one not listed is "a guitar (not listed)" ("an
    instrument (not listed)" for something else), and a free take's sidecar gets
    `instrument` (the kind).
  - **Tempo:** a control that does not apply is off and says why ("120 bpm, set by the
    backing"; "No click: the backing plays instead"; a free take has no click or count-in).
  - Beside Record, the next take in a sentence (what, over what, on what); while recording,
    the count-in and the bar there instead.
  - Tried on the desktop (fake device, headless Chromium at 1180x820, 820x1180 and 390x844,
    light and dark): each mode's request, a backing added while the page was open (listed
    within 9 s), a free take on "other:guitar" (its sidecar: "a guitar (not listed)",
    `instrument` guitar). Record ends at 667 px over the click and 711 px over a backing, in
    820 px. Not yet deployed.
- **The page, redesigned** (2026-10-16). The player's direction: "there are two main tasks:
  choosing what to record, and recording it"; from the design canvas, steps with one question
  each ("P1 is okay. dark theme, purple a key color"), as quick as the one-screen version for a
  take like the last. Dark, purple for what to tap.
  - **Choosing:** four steps. What you are recording on (one or two instruments, and an inputs
    panel: what each input carries, with its level). What sort of take (exercise, play-along,
    free play). Which exercise, which backing, or anything from the speakers. Then Ready.
  - **It opens on Ready, as it was left.** The choices are kept on the control unit
    (`GET`/`PUT /api/choices`); with none, they start from the newest take. The steps along the
    top hold the choices: a tap on one changes it, and a change goes straight back to Ready
    unless it leaves a later step unanswered (switch from the dobro to the bass, and the dobro
    exercise needs a new pick). Ready offers the next exercise the project wants (Or next, Swap).
    With two instruments there is no exercise; "Just the ..." narrows to one.
  - **Recording:** the record screen (the score and its instructions, the inputs' levels, the
    speakers, Record). Then the take (the count-in counted down, the bar and beat, each input's
    meter and peak, Stop). Then what was just recorded: its waveform and beat 0, play on the
    speakers or the iPad, a note, Record it again, Record something else, delete, and the
    exercise's other takes.
  - **New in the API:** `/api/inputs` (the config's `[inputs.N]`: what is plugged into each
    input, and for which kind of instrument); `/api/monitor` (the inputs' levels between takes,
    for 5 s after the page last asked); each channel's meter in the status;
    `/api/backings/{id}/play` (a backing heard on its own); each backing's `added`;
    `/api/takes/{id}/peaks` (a waveform, read a second at a time); a free take over a click,
    until stopped (`playalong.ClickStream`, sample for sample `click_track`; the sidecar's
    `click.beats` says how far it went, to line the take up again).
  - **The sidecar** gets `kind` (exercise, playalong, free), `instruments` (one or two:
    `instrument_id`, `instrument_name`, `kind`) and `channels` (each channel's input, its name,
    and what it carried: an instrument's id, `room` or `none`). With one instrument,
    `instrument_id` as before; with two, none, and no `instrument`.
  - **Fonts:** Bricolage Grotesque and Public Sans, served by the app
    (`exercises/static/fonts/`, SIL Open Font License 1.1), so the page needs no internet.
  - Tried on the desktop: the fake device now has four channels (each 6 dB under the one
    before), kept like the 18i8's three; headless Chromium at 1180x820, 820x1180 and 390x844.
    Every step, a change with a knock-on, an exercise take over its click, a play-along with
    the backing heard first, a free take over a click (lined up again from its beats), and the
    takes' note and delete. No console errors.
  - **Deployed 2026-10-16, about 06:15** (both recorders idle). `/etc/exercise-recorder.toml`
    got the `[inputs.N]` tables by hand (the installer keeps an existing config; the old one is
    `/etc/exercise-recorder.toml.pre-inputs.bak`). 2026-10-16: the DI moved to rear line input 7 (the
    player; 5 and 6 carry the mixer for records), kept as channel 2 in place of input 1, so a take's
    channels are as before; edited by hand on the unit (`/etc/exercise-recorder.toml.pre-input7.bak`)
    and in `config/exercises.toml`. Not yet tried on the LIVA: the practice
    recorder recording while the exercise page is open (the inputs handed over on its yield).
- **The score before choosing, breadcrumbs, and hearing the chart** (2026-10-16, the player's
  request: "i want to see the chart previewed for the selected exercise. i want breadcrumbs at the
  top to take me back to an earlier step instead of just all the way back to 'music room' index. i
  want a renderer to be able to play a synthesized version of the chart ... when i'm in the
  ready-to-record state for an exercise").
  - **The picker shows the score.** A tap on an exercise shows its pages beside the list (an
    iPad held landscape), or as a sheet over it (narrower, and phones); "Use 028: back to Ready"
    takes it. It opens on the exercise chosen. At 1180x820 the list now scrolls in its column.
  - **Breadcrumbs** replace the lone "Music room" link: Music room, each step with its choice
    (the instrument, Exercise, "027 Slides into Vibrato"), Ready, Record, the take. Each goes back
    there with the choices kept, as the step chips did. While a take records they are not links
    (a free take would go on with no one to stop it). A take's note typed but not sent is sent
    before the screen changes. The mixer's name and the free space moved into the record screen's
    Listening box. One line at 1180 and 820 wide (the exercise as its number at 820); a phone
    scrolls them sideways, opened at the end.
  - **Hear it** (Ready's What row, and the score's bar on the record screen) plays the chart over
    the speakers at the score's tempo, with the take's count-in and click as Ready's switches set
    them, and shows the count-in, then the bar and beat. Over the speakers, not the iPad: the
    iPad's speaker cannot play a bass's low notes, the level is the one the take's click will
    have, and the engine plays one thing at a time, so a take starting stops it before its
    capture starts (the fake device hears its own playback: a tone playing when Record was tapped
    does not reach the take, test_exercises). Leaving Ready and the record screen stops it too.
  - **The chart is rendered on the desktop at push time** (`exercises/chart.py`; the control
    unit only plays the file): the tab staff's notes in play order (as the click counts them),
    ties joined, chords, rests, picked or not (legato slides, hammer-ons, pull-offs, slurred bow
    changes), slides and scoops as pitch bends (a channel per string, two by turns), vibrato from
    the words over a note ("slow, wide vib.": 4 or 7 a second, 0.12 or 0.45 semitones), "soft"
    and "loud", "arco". Through FluidSynth (a program, not in the repo) with the player's own
    SoundFonts for the dobro (the Beard, `dobro.sf2`) and the bass (the Cleveland, `upright.sf2`),
    GeneralUser GS's double bass for arco, steel-string guitar and banjo; looked for in
    `$EXERCISE_SOUNDS`, then `~/play/ambient-room/sounds`, `~/.local/share/sounds/sf2`. Mono,
    48 kHz, 16-bit, beat 0 at 0.5 s: `chart.wav` beside the pages, and `audio` in exercise.json.
    The app brings it to the takes' loudness and lays the take's click under it
    (`playalong.with_click`, the same `click_track`).
  - **Checked on the desktop:** the notes as read agree with dobro-transcription's answer-key
    reader (`transcribe.score.read_score`) on all 64 exercise and tune scores: onset, pitch,
    length, picked or not; and the beats with push-exercise's count. Its bench, run on the
    renders of 14 (dobro and bass): every note in its bar, no rhythm errors, notes F1 0.97 (the
    misses are double stops, as in its takes). Each note's pitch within 30 cents of the score
    (2134 notes; pyin's median +1 cent). Notes speak 5-20 ms after their beat (a few dobro
    samples about 40 ms), with no drift. The samples run out: the Beard's last 1.2-7.6 s (G4
    2.9 s), the Cleveland's 0.7-4 s (A2 and D3 0.7 s), so a note held longer dies away early.
    63 exercises rendered in 5.5 s, 174 MB.
  - **Existing exercises:** `tools/push-exercise --audio-only [ID ...]` renders the audio again
    for what is on the control unit (or `--to` a folder), from the score pushed with each, and
    puts `chart.wav` and then exercise.json back by renames. `--no-audio` pushes without it.
  - Tried on the desktop (fake device, headless Chrome at 1180x820, 820x1180 and 390x844): the
    preview, the walk from the top, a change of instrument that leaves the exercise to pick again,
    Hear it on both screens, Record while it plays (it stops), the crumbs during a take. No console
    errors. Deployed 2026-10-16 (evening), with the chart audio rendered for all 63 exercises on the control
    unit (`--audio-only`, 184 MB there). The player chose their own SoundFonts where there are
    any (the Beard, the Cleveland), GS for the rest, after an A/B with GS's steel-string guitar
    and acoustic bass. Not yet heard over the speakers on the unit.
- **The board** (2026-10-19, the player's request: "i want the index of the exercises app to be a board
  showing which exercises are wanted, by instrument, so i can jump straight to an exercise i want to record.
  (an instrument-specific screen could remain as an optional narrower index / starting point)").
  - **The app opens on the board** ("What's wanted"), not on Ready. A column for each instrument with
    something wanted: wanted again (with why), then no take yet, then what was recorded here since and is
    waiting for the project to bring it in (dashed, "Recorded today 3:58 PM"). The instruments with nothing
    wanted are named at the top, as links to their lists. Above the columns: Play-along, Free play, and
    "As you left it" (the next take as saved, "Back to it" to its record screen).
  - **A tap sets the exercise up and opens its record screen** (the score, Record): on the instrument of
    its kind played last, with the inputs that take had (else the first the project knows of that kind,
    else "a banjo (not listed)"). Ready stays as the screen for the click, count-in and tempo (the record
    screen's "over the click at 80", or the crumb).
  - **An instrument's list** (the old picker: what it wants first, the good ones, the tunes, the score of the
    one tapped) is "All 29" on its column; "Set up 026" opens the record screen. `#dobro` in the address
    opens it, so a bookmark can start there. The page keeps its screen in the address (`#ready`,
    `#record`, ...), so a reload (Safari drops tabs it has not shown for a while) comes back to it.
  - **The steps:** "What sort" is gone (the board asks it). A play-along or free take walks from the board:
    on what, which backing or anything from the speakers, Ready. The crumbs: Music room, Exercises (the
    board), the instrument (On what), the exercise or backing, Ready, Record, the take. "Record something
    else" goes to the board.
  - Also: the record screen's sentence at 32 px (44 before), so the score starts higher; the voice is "your
    voice" ("with your voice"); exercises sharing a number (the voice's 001s) are named by title in Ready's
    "Swap to" and the crumbs; an exercise the project does not list (`dobro-021-hammer-or-pick-doubled`) is
    "Not in the project's list", not "Needs a take"; a long "wanted again" note no longer spills out of
    its row; choices not yet sent go when the page is hidden or unloaded.
  - Tried on the desktop (fake device; the unit's 94 exercises, status and 24 takes mirrored over its API,
    the audio silent; Chrome headless via Playwright at 1180x820, 820x1180 and 390x844): the board, a jump,
    the crumbs, an instrument's list, Set up, Ready, the voice, a play-along walked and backed out of, a
    reload on `#dobro`, a take over the click and "Next wanted" from it. No console errors, no sideways
    scroll. The tests pass (142). Deployed 2026-10-19 at 16:02 (everything idle).
  - **Still open for the exercise app** (`docs/ui-issues.md`): the score's room on the record screen and during
    a take (the sentence is smaller; the side column and the score's own scale are not yet looked at); not yet
    used on the iPad.
- **Exercises for any instrument** (2026-10-19, the player: "i want the relevant exercises to be
  instrument-agnostic and key-agnostic (like, major scale, other scales, chords, simple melodies)"): dobro-transcription's
  `exercises/generic/` (21: scales, arpeggios, strummed chords, techniques, five public-domain tunes), one treble
  staff with no tab, played in any key on any instrument (the mandolin, guitalele, banjo, mountain dulcimer...).
  - **Their kind is `generic`** (ids `generic-NNN-...`; `tools/push-exercise` names it from the id or the part
    "Any instrument"), shown as "Any instrument": a column of its own on the board, at the end. Their chart is
    heard as a nylon-string guitar (GeneralUser GS's, program 24).
  - **What it is played on.** A tap on one opens its record screen on the instrument played last for these (the
    choices' `anyInstrument`, else the newest such take); with none yet it first asks ("What are you playing it
    on?"): any instrument the project lists, any kind it has exercises or takes of, the player's own (mandolin,
    guitalele, mountain dulcimer: `SUGGEST`), or a kind typed in ("Another kind", on every take's "On what" now).
    "Something else" (no kind) is not offered: the take must say. The record screen has "Played on": the
    instruments these were played on, one tap to change, and "Something else" (the step, then back to the record
    screen).
  - **What is still wanted, on the instrument in hand.** status.json gives each its `played_on` (the kinds the
    project has takes of it on, from takes.csv's `instrument`). The column, its list and the record screen say
    what is wanted on the kind played last ("Needs a take on the mandolin", "20 to record · on a mandolin (not
    listed)"), and what was recorded here on it and not yet brought in.
  - **The sidecar:** `instrument` is the kind it was played on (as a free take's), `exercise_instrument` "generic",
    with `instruments`, `instrument_id` and `instrument_name` as before. A take of one that names no kind is
    refused (400).
  - **Bringing a take in:** `tools/pull-take --exercises TAKE_ID DIR` copies an exercise take (its config
    `/etc/exercise-recorder.toml`), then dobro-transcription's `exercises/bring_in.py DIR/TAKE_ID.wav --session ...`
    puts it in its folder: raw/, the instrument's channel as take-NN.flac, the `takes.csv` row (a generic folder's
    with `instrument` and `instrument_id`) and `takes.md`. For any exercise, not only these.
  - Tried on the desktop (fake device; 72 exercises from an older mirror and the 21 pushed with `--to`; headless
    Chrome via Playwright at 1180x820, 820x1180 and 390x844): the column, a first tap asking, a mandolin chosen
    and its record screen, the instructions, a take and its sidecar, the board after it (20 to record, 001 waiting
    to be brought in), the next one straight to its record screen, a kind typed in and back, the list on the
    mandolin; a dobro exercise, a free take's steps with a kind typed (and one refused), `#dobro`. No console
    errors, no sideways scroll. The tests pass (147). Not deployed.
- **Next:** take a play-along's backing out on the desktop instead of the Pi.

### 7. The chord app (started 2026-10-16)

The player's request: "let's add a stripped down beginning of live detection as a fourth music room
app... just showing the currently playing chord as detected. this might benefit from some piano work
where it will be easy to play a chord, check for accurate detection, and give feedback". For making
dobro-transcription's live chord detection more accurate (the band that follows hears chords the
same way).

- **A fourth program on the control unit:** `chords/`, port 8003, `setup/chord-listener.service`, its
  own config (`/etc/chord-listener.toml` from `config/chords.toml`). It only listens: the inputs the
  exercise app keeps (3 the Nadine, 4 the condenser, 1 the piezo), mixed as the page chooses (the
  room mic unless told).
- **The listener is the band's own:** `chords/livechords.py` is dobro-transcription's
  `transcribe/livechords.py`, copied as it is by `tools/sync-livechords` (a test fails when the two
  differ). Every 100 ms the last 0.4 s are heard (each spectrum peak at its semitone, a treble and a
  bass chroma, 36 chord templates: major, minor and seven on 12 roots), the chord shown smoothed by a
  forward HMM (stays put with probability 0.85 a frame). Nothing is named within 12 dB of the
  quietest of the last 10 s, or under -50 dBFS + 12 dB before anything that quiet is heard.
- **Listen:** the chord, large, its runners-up and recent chords; Right, or Wrong: it was... (a grid of
  the 36, or no chord) keeps the last 2 s with the player's label.
- **Check:** a chord is asked for (major, minor, seven; all 12 roots or G C D A E F Bb); the next
  attack (a rise of 9 dB within 0.3 s) starts it, and the chord is judged over 0.1-1.1 s after it,
  once the strike has passed: right or what it heard, at once; the clip (every kept channel, from
  0.3 s before the attack to 1.6 s after) is kept when its end has been heard. "I played it wrong"
  throws the clip away. A tally of the session, and its misses.
- **Kept for study:** `~/chord-checks` on the control unit, one WAV and a JSON sidecar per check or
  label (asked, heard, right, the top scores, the instrument being played, the inputs mixed, which
  listener judged); `tools/pull-checks DEST` copies them to the desktop.
- **Sharing the inputs:** it listens only while its page asks (5 s each time), and lets them go for
  10 s when asked (`/api/yield`): the practice recorder and the exercise app now list it among their
  peers, so they ask before they record (and before they play a take, when it pauses 10 s and comes
  back). The ambient synth does not list it: it does not use the inputs.
- Tried on the desktop with `--demo` (chords made by the program in place of the inputs): both views
  at 1180x820 and 390x844, a check and two labels kept, no console errors. On synthesized chords the
  listener names 33 or more of the 36 (a test).
- **Inversions, diminished chords, single notes and two** (2026-10-16, the player: "it would be good
  if it could handle inversions and indicate what's in the bass"; "no detection of diminished chords";
  "extend detection to handle a single pitch or diad as well"). From dobro-transcription's listener:
  what is in the bass (the lowest strong peak, E1-E4) named on its own, "C/E" when not the root; chords
  scored with any of their tones in the bass counting nearly as the root; a wider vocabulary for the
  app (major, minor, seven, diminished, diminished seven: 60 chords; a diminished seven named by its
  bass); and how many notes sound (pitches found one harmonic series at a time), so one pitch class
  shows as a note ("E3"), two as a pair ("C+G, a fifth"), three or more as a chord. Checks ask for
  any of these (inversions, notes with their octave, pairs in any octave), a chord judged on the chord
  and the bass apart; the label picker has the three kinds. On synthesized tones: 55 or more of the
  60 chords, the inversions and the bass, notes and pairs (tests). The band's own scoring is unchanged. Deployed
  2026-10-16, about 07:35 (everything idle), with the practice recorder's page and the home page in
  the exercise app's look (no config changes).
- **Play one** (2026-10-16, the player: "the ui for right and wrong is confusing because detection
  keeps changing while i try to input feedback... a dedicated feedback ux that lets me play a single
  chord then give feedback if it's wrong and be able to play the clip to confirm how it got
  recorded"). A strike: the next attack is heard and judged as a check is, with nothing asked for, and
  held (the chord and its bass, the notes, the runners-up) until the player answers: Right, Wrong: it
  was... (the picker), or Throw it away; then the next. Hear it plays the kept clip on the iPad (the
  mixed inputs, peaking at -3 dBFS, 16-bit mono: `/api/checks/{id}/audio?mix=1`); a Check's result has
  it too. The answer goes into the clip's sidecar (`/api/checks/{id}/label`: what it was, and whether
  that is what was heard). Listen is a display only now.
- **Tempo, the loop, and how sure** (2026-10-16, the player: "show tempo if a steady tempo is
  detected, and show chord form if that is detected as well... cycle between a few chords in
  reasonable time and have it recognize the loop and display it"; "some indication of confidence for
  each of the things it detects. maybe a little power meter type thing"). From dobro-transcription's
  listener: `Pulse` (an onset envelope's autocorrelation over the last 12 s, every 0.5 s; steady when
  its peak is 0.3 or more three times running within 4%; the period folded into 70-140 bpm: a chord
  every 2.5 s is 96 bpm, a strike every four beats) and `LoopFinder` (a chord counts once it holds
  0.5 s; the loop the shortest cycle of 2-8 changes the last two rounds repeat; one wrong chord let
  pass, a second drops it; 4 s without a chord clears it). Listen shows the tempo, the loop with the
  chord the playing is on and each chord's length in beats, and for each reading five bars of how
  sure: the chord (its smoothed probability), the bass and the kind (how many of the last 5 frames
  agree), each note (its level against the loudest), the tempo (the pulse's peak), the loop (its
  rounds, halved after a chord let pass). A new listen starts the tempo and the loop afresh. Deployed
  2026-10-16, about 08:04 (everything idle), with Play one and the old mixer note's removal.
- **Listen on three time scales** (2026-10-16, the player: "it's pretty frenetic... if I'm playing an
  arpeggio of Am then G, back and forth, i see the notes moment by moment, the chord and tempo bar by
  bar, and the current spot in the chord cycle (so three zone: this instant, this bar, and overall
  orientation). the non-instant ones are blank or just show a low confidence meter until they have
  enough tracking to show something"). Listen is now three panels:
  - **This moment:** the notes sounding, each with its level, what they make (small), and the input
    level.
  - **This bar:** the chord over about half a bar (`livechords.ChordPool`: the notes heard, pooled by
    pitch, fading over two beats once there is a tempo, else 0.8 s), large, with its bass. It is shown
    from 0.4 sure: the fit, times how much has been heard, times the share of its tones there, so one
    note of an arpeggio is not yet a chord. Below it, the tempo, with a dot lit on each beat (the page
    keeps the beat between polls, from the pulse's period and the time since a strike).
  - **Where you are:** the loop of those bar chords, the one the playing is on lit, the next one
    marked, the rounds; until there is one, what it needs.

  Each panel has its five bars. The runners-up and the recent chords are gone from Listen. The pool
  keeps the chord before from lingering: when a note outside the chord starts, what was pooled and has
  stopped sounding fades fast, so F's residue does not make G a G7. A new chord needs two of its notes
  started, or what it adds to the one shown (Am to F; C to C7). Its bass is the lowest of its own notes
  started since it began, so an arpeggio's first note is the bass, and the chord before's late bass is
  not. The loop gives way to another found the same way, and goes once two chords in its last two
  rounds are not its own. Before, an Am-G loop held on through C-Am-F-G. The home tile shows the bar's
  chord once it is sure. Deployed 2026-10-16, about 08:50 (everything idle).
- **Checks at every register, and chords that don't ring** (2026-10-16, the player: "the chord
  detector gets many wrong. some octaves are fine and others are not. chords that don't ring are not
  detected well"). Studied on the 32 clips kept that morning (16 labels 07:14-07:47, 16 checks
  08:12-08:15; piano, the room mic). `tools/check_study.py DIR` judges each again through the app's
  `Listener._judge` (`--livechords` for another listener, `--old-span`, `--compare`, a CSV) and
  breaks the results down by register (the octaves of the lowest and highest notes played, read from
  the spectrum against the target) and by ring (damped: under 0.4 s to 20 dB under its peak; short:
  under 1 s; rings; held: a label with no attack; faint: no target note over the room). The replay of
  the deployed code gives every check's kept verdict (16 of 16).
  - **What failed, as deployed** (8 of the 16 checks right, 20 of the 32 clips):
    - Chords over E4: the bass was the lowest strong peak from E1 to E4 only. F5-A5-C6 was F/F#
      (the room's hum its bass); F4-A4-C5, A4-C5-E5 and C6-E6-G6 had none.
    - Chords that don't ring: a check was judged over 0.1-1.1 s after the attack whatever the chord
      did. A C damped at 0.25 s had 2.6% of that window's weight, and half the window's energy came
      after it; the soft F near F1 was over before the window opened. A chord with nothing in the
      bass chroma's range (E1-D3) got a bass chroma of the strike's thump (near 115 Hz, 20-25 dB
      under the chord): the chroma is scaled by its own sum, so the thump counted in full. C was C7.
    - Low chords: partials taken for notes (a low root's seventh partial made C7 and F7; G#2's made
      Abm of an E).
    - `attack_s` (0.3) and `judged_s` ([0.1, 1.1]) were constants: where the attack is in the clip,
      and the span after it. The attack is found a block (0.1 s) at a time: the onsets were 0.01-0.09
      s after it, and once 0.04 s before.
  - **Changed:** a check is judged from 50 ms after the chord's onset (the steepest 10 ms rise
    within 0.1 s of the attack) until it falls 30 dB under its peak or to 10 dB over the room, 0.2 s
    at least, up to 1.1 s after the attack as before. Its bass is looked for from E1 to the top of the
    pitch axis, and a chord whose bass is over D3 is scored without the bass chroma. For the piano, the
    span is judged by the merged listener's piano detector (`hear_piano`: notes A0-C8, the chord they
    make, the lowest note the bass; what rang in the 0.3 s before the onset taken out only when the
    chord rose over it). Other instruments keep the chroma. A clip keeps the second before its attack,
    and its sidecar the onset, the span judged, how long the chord sounded and its levels.
  - **Results** (the 32 clips / the 16 checks right): as deployed 20 / 8; the new span and bass with
    the deployed listener 26 / 13; with the piano detector 28 / 15. Of the checks: damped 0/3, 2/3,
    3/3; short 2/5, 5/5, 5/5; ringing 6/7, 6/7, 7/7; lowest note in octave 4 1/3 to 3/3, in 5 0/2 to
    2/2, in 6 0/1 to 1/1. Nothing right before went wrong, except one held label with the piano
    detector (a Bb heard Bb/F: an F2 33 dB under the Bb2, played softly or ringing in sympathy).
    Still wrong: the soft F near F1 (18 dB over the room, no fundamental clear) and the C4 and D3
    labels, whose clips hold no note (its partials at -95 dBFS, within 10 dB of the floor).
  - **What holds and what doesn't:** the span's settings were reasoned, not fitted, and the counts
    stay within one check over 0-0.05 s from the onset, 25-60 dB under the peak, 3-20 dB over the room
    and 0.1-0.6 s at least; 0.08 s or more from the onset loses the damped chords. These are 32 clips
    with 3 damped checks: the counts move by single clips. The register is the study's estimate; it
    agreed with a reading of the spectra on 31 of the 32 (the F near F1 is the player's). On
    synthetic chords damped at 0.2-0.5 s with the bass in octaves 1-3, the piano detector is right on
    14 of 27 over the new span and 9 over the old: short low chords are still the weak spot.
  - Tests: `tests/test_checks.py` strikes chords with the piano's partials (its tuning and stretch)
    over a room's hum and each strike's thump, damped and ringing, A0 to G6, on both paths. DemoSource's
    checks judge as "other": its partials are exact harmonics, and the piano detector hears its Dm as
    D2 and F3.
- **Listen hears the piano by the piano detector** (2026-10-16; deployed about 10:15 with the checks' piano detector, everything idle). With the piano chosen,
  this moment's notes, what they make, and the bar's pool come from `livechords.piano_notes` (each 0.4 s
  window's peaks, the notes on another note's partials found too), the chord from `chord_of_notes`, the
  bass the lowest note. Other instruments keep the chroma path. The frame where a note is struck is
  skipped (`STRUCK_DB`: a block 6 dB over the one before), since a note struck at the window's very end
  shows its partials before its fundamental stands out and is read as several. The room's hum and
  noise are learned from each second of the room alone (within `ROOM_DB` of the quietest of the last
  10 s, itself under `QUIET_CEIL_DBFS`, so not a chord's tail) and taken out of the peaks. The demo now
  plays this piano's stretched partials (`piano_tone`), so the detector hears it. On the LIVA the
  detector took 91 ms a 0.4 s window (measured just after the deploy, on a synthetic chord; 4-6 ms on the
  desktop; `window_salience` 7 ms), nearly the whole 100 ms a frame has: so the Listen view runs it only
  as often as costs `PIANO_BUDGET_MS` (35 ms) a frame, what it last heard in between (every third frame
  on the LIVA; afresh the frame after a strike), redeployed at 10:24. `/api/status` reports
  `frame_ms` and `piano_ms`. A check (judged once) takes the detector's full cost once. The detector
  made 3-5 times faster in dobro-transcription (the same results to the bit), deployed 2026-10-16
  evening: on the LIVA (Celeron N2807) 15.2 ms a frame over three chords (median 13.9, slowest 22.6)
  against 135 ms before (`pipeline/tools/piano_cost.py`, the same notes heard), under the budget, so
  the Listen view runs it every frame there now.
- **Deployed 2026-10-16, about 07:10** (both recorders idle). The installer added
  `/etc/chord-listener.toml` and the unit; by hand, the peers line of `/etc/practice-recorder.toml` and
  `/etc/exercise-recorder.toml` and the tile in `/etc/room-home.toml` (copies kept as
  `*.pre-chords.bak`). Not yet tried on the LIVA: listening to the 18i8, or a recording taking the
  inputs from it.

### 8. The transcribe app (started 2026-10-19)

The player's request: record a take on the phone and see the transcription pipeline's notation and tab for
it right there, with playback; "Review it" sends the take to the review app (dobro-transcription's, port
8005), whose answers turn the draft into the take's answer key.

- **A seventh program on the control unit:** `transcriber/`, port 8006, `setup/take-transcriber.service`, its
  own config (`/etc/take-transcriber.toml` from `config/transcribe.toml`) and take folders
  (`~/transcribe-takes`). The exercise app's devices and inputs (the 18i8; inputs 3, 4 and 7). It uses the
  recorder's engine, click and line-up, and takes turns as the others do (`peers`: 8000-8004; the practice
  recorder, the exercise app, the ambient synth and the band list 8006). The chord app asks no one, so it
  needs no change. A tile on the home page.
- **The look and flow** are the exercise app's redesign (step 6): dark, purple for what to tap, steps that
  open on Ready as left, the choices kept on the unit (`/api/choices`).
  - **Choosing:** two steps. What you are playing (the dobro or the bass, `[instruments.*]` in the config; the
    piano too since 2026-10-19, below) and which input carries it (the dobro the room mic, input 4; the bass the
    Nadine, input 3, as the corpus's bass takes; any kept input can be chosen, with its level). Then the take: the tempo, the beats in a bar
    (4, 3 or 2), a bar's count-in (on), the click all through (on), a name if wanted. A new instrument or
    input chosen from Ready goes straight back to it; the take's step has its "Back to Ready".
  - **Recording:** the record screen (the input's level, the click's level on the speakers, Record), then
    the take (the count-in counted down, then the bar and beat; the input's meter and peak; Stop).
  - **The take:** first how its transcription goes (the stage, the time, an estimate from the takes this
    unit has done), with its sound playable meanwhile; then the tempo, the meter, the bars and notes found
    and how long transcribing took, the notation and tab (or the tab alone), and the take's own sound at
    full or half speed with a cursor over the beat played and its bar lit. A tap on a bar plays from there.
    "Review it", rename, delete (asked on the page), the FLAC and the MusicXML.
  - **Takes:** newest first, to reopen, rename or delete.
- **The timing:** over the click, `Engine.start_click` with no last beat (`playalong.ClickStream`, as the
  exercise app's free take); the count-in alone with the click off (new: `ClickStream(through=False)`, the
  count-in and then silence until Stop; `click_track(..., through=False)` the same sample for sample, so a
  take lines up again from its sidecar); neither, a plain take. The click is found in the take as the
  exercise app finds it (`find_click`, over every kept input): bar 1's downbeat.
- **After the take** (`transcriber/jobs.py`, one take at a time):
  - **Its folder,** laid out as a folder of the corpus (dobro-transcription `tunes/<instrument>/<name>/`;
    `transcriber/store.py`): `take-01.flac` (the instrument's input alone, mono, 48 kHz, 24-bit FLAC),
    `takes.csv` (the corpus's columns, `read_takes` reads it), `meta.json` (instrument, bpm, beats_per_bar,
    count_in_bars, click, title, recorded_at, the app's version; the input, the downbeat found, the
    transcription's state and time, the review's id), `raw/` (every kept input and the engine's sidecar),
    `play/` (the page's sound), `transcription.musicxml` and `transcription.json`. The engine's own copy is
    deleted.
  - **Its sound for the page:** `play/full.mp3` and `play/half.mp3` (ffmpeg's `atempo=0.5`: half speed with
    the pitch kept, the review app's way).
  - **The transcription:** `transcriber/job/run_pipeline.py` in the review app's venv on the unit (numpy 2.2.4,
    scipy 1.18.1, librosa 1.0.0, soundfile 0.14.0), with `~/dobro-transcription/pipeline/src` on its path, at
    nice 19 and two threads; held still (SIGSTOP) while this app records, and none starts then. Over the
    click: `--metronome --downbeat S`, the score's beats the click's. With the click off: the beat tracker
    from the tempo given (`transcribe.chords.beat_grid`), whose tempo the pipeline is told when within 15%
    of it, and `--downbeat` from the count-in if there was one; the score's beats are placed by its notes.
    `transcription.json`: each note's time, beat, bar, pitch, string, fret, technique and length, and every
    beat of the score in the take's time (what the page's cursor follows). `takes.csv` then gets bar 1's
    downbeat as `beat0_s` (the one told, else the transcription's: what the review side reads while the
    take has no key), the tempo transcribed at and the pickup.
- **The notation:** alphaTab 1.8.4 (MPL-2.0; the npm package's `alphaTab.min.js`, its Bravura font under the
  SIL OFL 1.1, `transcriber/static/alphatab/SOURCE.txt`), served by the app: no CDN. It reads the pipeline's
  MusicXML as it is; for drawing only, the tab staff's voice 5 becomes voice 1 (alphaTab drew an empty voice 1
  there as a rest in every bar). The page plays the take itself and puts the cursor where `transcription.json`'s
  beats say. It signs the score "rendered by alphaTab".
- **"Play the transcription"** (2026-10-19; the player, after the first real use: "the transcriptions should
  include a 'play' button to play a render of the transcription itself"): beside "Play the take" at the foot of
  the take, the score as written, played by alphaTab's own player (its synthesizer, in a worker) with alphaTab's
  cursor on the beat played and its bar lit, the played bar kept in view. One sound at a time: either button
  stops the other. Full or half speed applies to both. A tap on a bar, or on the position bar, plays the sound
  last chosen from there; the position bar shows the take's time for the beat the transcription is on.
  - **The SoundFont:** `sonivox.sf2` from the same npm package (`dist/soundfont/`, 1,351,896 bytes; SONiVOX EAS,
    Apache License 2.0, Copyright 2004-2006 Sonic Network Inc.; `static/alphatab/soundfont/LICENSE`), served by
    the app at `/alphatab/soundfont/`. It loads when "Play the transcription" is first pressed (the player is
    made inside that tap, so the audio may start), and the browser keeps it a day.
  - **The sounds:** General MIDI programs (0-based), set on the score's track when it loads: the dobro 25,
    Acoustic Guitar (steel), the nearest; the bass 32, Acoustic Bass; the piano 0, Acoustic Grand Piano.
  - **Each note once:** the pipeline writes every note on both staves (the notation, and the tab), and alphaTab
    plays every staff. In alphaTab's `midiLoad` event (the MIDI before the player gets it) the second note-on of
    a pitch at the same tick is taken out (the tab's kept: its pull-offs and hammer-ons), with the matching
    note-offs and bends. Today's dobro take: 170 note-ons to 85, its 85 notes.
  - **To change the sound by ear:** the page's `SOUND` constant (the font's path, a program for each
    instrument); a font is put in `static/alphatab/soundfont/`. The room's own fonts (ambient-room
    `sounds/dobro.sf2` and `upright.sf2`) are prototypes the player says don't sound right yet: a later option,
    not used now.
  - **Tried on the desktop** in headless Chrome at 390x844, 820x1180 and 1180x820, on today's take (35 s,
    17 bars): the transcription played (the cursor moving, the take's time 0 to 3.7 s after 2 s), the take
    played after it and stopped it, half speed, a tap on bar 3 playing from it, no console errors, no sideways
    scroll. Over `http://` from another host (as `practice.local` is reached) the browser gives no secure
    context, so alphaTab plays through a ScriptProcessor rather than an AudioWorklet (Chrome warns it is
    deprecated); it played the same. Tried again the same way once moved onto the piano's commits (ff4e8ac):
    the same. A piano take's grand staff should play with program 0 (its two staves hold different notes, so
    nothing is taken out); not tried in a browser yet, with no piano take on the desktop or the unit.
  - The review app's take check plays its bars' scores the same way (dobro-transcription `docs/review-app.md`,
    "Played as written"), with the same font. Not deployed.
- **"Review it":** `cd ~/dobro-transcription && PYTHONPATH=.:pipeline/src review/.venv/bin/python -m
  review.make_review take-check FOLDER --install local`, in the background; the id from its first line,
  kept in `meta.json`; the page links to `http://<host>:8005/#/r/<id>` (the review app's links). Until
  `make_review.py` lists `take-check` among its kinds (looked for each time the page loads), the page says
  the review app doesn't check takes yet.
- **Bringing a recording in:** `tools/import-take FILE --instrument dobro --bpm 100 [--click]
  [--downbeat S] [--channel N] [--title T]` on the unit makes a take folder of it and has the app transcribe
  it.
- **Tried on the desktop** (fake device; the pipeline from `~/play/dobro-transcription`, Core i7-12700K; nice
  19, two threads): takes over the click, with the count-in alone, and with neither, on the dobro and the bass,
  the folder as above (read by `read_takes`). Transcribing, by `tools/import-take`: Red Wing (80 s) over its
  click 4.0 s, as free play from 96 bpm 15.7 s (the tracker found 99.4; the quantizer's tempo search without a
  click is the slow part), the bass's Cherokee Shuffle (162 s, from its downbeat, no click) 18.5 s; 527 MB at
  the most for Red Wing. Rendering both mp3s: under 1 s. "Review it" against the review side's take-check as it
  was being written (its worktree): the review made in 8 s, linked as `#/r/take-check-2026-10-19-075545`. The
  page in headless Chrome at 390x844, 820x1180 and 1180x820: every step, a recording, the take drawn, played,
  the cursor following, half speed, a tap on bar 3 playing from 5.18 s, the takes renamed and a delete asked;
  no console errors, no sideways scroll.
- **On the unit (estimated, not measured):** its Core i7-5500U runs one thread about 2.5-3.5 times slower than
  the desktop's, so Red Wing over a click about 10-15 s, as free play 40-55 s, a 3-minute bass take 45-65 s.
  The page's first estimate is 0.4 s a second of take until the unit has done some.
- **Not yet deployed.**
- **Deployed 2026-10-19 at 08:21-08:23.** The peers on the unit by hand first (`/etc/*.toml.pre-transcribe.bak`
  kept): 8006 added to the practice recorder's, the exercise app's, the ambient synth's and the band's lists, and the
  tile to `/etc/room-home.toml`; then `tools/deploy` (the recorders, the chord app, the home page and the transcribe
  app restarted), and the ambient synth and the band restarted to read their peers. On the unit: Red Wing's take 1
  (80 s) imported by `tools/import-take` over its click, transcribed in 14.0 s (183 notes, 32 bars); "Review it" made
  `take-check-2026-10-19-082229` (8 questions) in about 5 s.
- **The piano deployed 2026-10-19 at 09:31**: `[instruments.piano]` (input 4, the room mic) added by hand to `/etc/take-transcriber.toml` (`.pre-piano.bak` kept), the review app's `take-check` with the piano deployed before it.
- **The piano** (2026-10-19, the player: "piano should be added as an option for transcriber"). A third
  instrument, `[instruments.piano]`, on input 4, the room mic (the MXL 990), as the exercise app records the piano:
  every piano exercise take's sidecar (2026-10-17) has input 4 carrying the piano and inputs 3 and 7 nothing, so a
  take's `take-01.flac` is channel 1 of the three kept.
  - **The run:** `--instrument piano` (dobro-transcription's `pianoscore`): the take read at its own rate (48 kHz),
    the beat tracker at the pipeline's 22.05 kHz. Over the click `--metronome --downbeat S`, as for the others (its
    count-in bar teaches the pipeline the click, kept out of the strikes); with the click off, the tracker's tempo,
    and the notes place the beats. The pipeline writes the piano's notes strike by strike from the top key down and
    finds a strike's keys in another order: they are sorted alike to pair them, so each note gets its own end. A
    note found before the downbeat puts the score's bar 1 a bar before it (as `pianoscore` writes it), and the
    beats' times follow.
  - **`transcription.json`:** each note's time, beat, bar, pitch, length (beats and s), end (where its damper came
    down, or null) and `staff` (1 the treble, 2 the bass) with its `hand` (right, left). No string, fret, technique
    or tuning (`tuning` null). The beats as for the others.
  - **The page:** a grand staff, no tab, and no Notation/Tab switch ("Grand staff: the right hand over the left").
    The bass staff's voice 5 is renumbered for alphaTab as the tab's was (a grand staff's 5-8 to 1-4: one voice a
    staff, as the pipeline writes it); every staff notation only, set in `scoreLoaded`; the cursor follows both
    staves' beats (a note's before a rest's, where both have one). The pipeline writes no pedal or dynamics in a
    transcription; the exercises' own scores (016's pedal, 017's dynamics) draw with them the same way.
    `/api/setup` gives each instrument `tab` (false: a grand staff), each take its `tab`.
  - **"Review it":** dobro-transcription's take check reads the piano from branch `take-check-piano` (its
    `review/take_check.py` names what it reads, `INSTRUMENTS = ("dobro", "bass", "piano")`; this app reads that line
    each time it is asked, and a take_check.py that names none reads the dobro and the bass, as first deployed).
    Until then a piano take's page says "Review isn't there yet for the piano", and `POST /api/takes/ID/review`
    answers 409.
  - **The first estimate:** 0.1 s a second of take for the piano (0.4 for the others) until the unit has done some.
  - **Timed on the desktop** (Core i7-12700K, nice 19, two threads): the piano exercise takes of 28-40 s
    (dobro-transcription `exercises/piano` 001, 008, 012-015) 0.4-0.5 s over their click, 2.1-2.5 s as free play
    (the beat tracker is most of it); five of them run together (187 s) 3.1 s over the click, 6.8 s free. The
    review of a 40 s take (012, three questions) made in 2.7 s (3.9 s from the page's tap), of the 187 s one in
    11.8 s. Memory: 241 MB for a 40 s take, 1.0 GB for 187 s (1.2 GB free), once the pipeline's `note_ends` took its
    spectra a block at a time (on the same branch; 583 MB and 2.5 GB before, the same notes and output).
  - **On the unit (estimated, not measured):** 2.5-3.5 times the desktop's times (Red Wing's 80 s take: 4.0 s here,
    14.0 s there), so a 40 s exercise take about 1-2 s over the click and 5-9 s free, a 3-minute piece about 8-11 s
    over the click and 17-24 s free, a 40 s take's review about 7-14 s. The unit has 3.8 GB (3.0 GB available,
    2026-10-19): a 3-minute piano take's 2.5 GB before the change above was too near that.
  - **Tried on the desktop:** exercises 008 (G1 to G7, both staves) and 012 (triads, right hand) brought in by
    `tools/import-take` against the dev config and transcribed (7 notes, 008's key exactly; 9, 012 missing two
    chords' top notes), with Red Wing's dobro take beside them. Headless Chromium at 390x844, 820x1180 and
    1180x820: the grand staff drawn (two staves, one voice each, no tab), played at full speed with the cursor on
    the beat, a tap on bar 3 playing from 12.25 s; the instrument step with its three cards; "Review it" made the
    review, whose excerpts the review page drew as grand staves. Red Wing still notation and tab, a tap on bar 3
    from 5.18 s. No console errors, no sideways scroll.
  - **Not yet deployed.** The order: dobro-transcription's `review/tools/deploy` (the review side and
    `pipeline/src`; it restarts the review app only); then `tools/deploy` here (every app here restarted; the
    unit's `/etc/take-transcriber.toml` is kept, so the installer notes it has no `[instruments.piano]`); then
    `[instruments.piano]` (`name = "Piano"`, `input = 4`) added to `/etc/take-transcriber.toml` by hand and
    `sudo systemctl restart take-transcriber`. Not the other way round: the app deployed now refuses a config with
    an instrument it does not know.
- **Any stringed instrument** (2026-10-19, branch `generic-transcriber`; the player: "a generic stringed-instrument
  transcriber... i have guitalele, and a mandolin, and a banjo, and a mountain dulcimer"). A fourth instrument,
  `[instruments.strings]` ("Any stringed instrument"), on input 4, the room mic: dobro-transcription's generic path
  (`transcribe --instrument strings`, its `transcribe/strings/`), which needs no tuning and writes one staff, no tab.
  - **Choosing:** its card, and under it "Which kind?": Mandolin, Banjo, Guitar, Guitalele, Ukulele, Mountain
    dulcimer, Dobro, Bass (`settings.KINDS`), or "Not sure" (the default). To the pipeline a kind is only a pitch
    range (`--kind`); not sure, it reads the range from the take. Tapping the card from Ready stays on the step, for
    the kind. The kind is in the sentences ("Mandolin at 90 bpm"), the step's crumb and the take's name;
    `POST /api/record/start` takes `kind` (refused unless it is one of `KINDS` on "strings"), and meta.json keeps
    `kind` and `kind_name`.
  - **The run:** `--instrument strings [--kind K]`, over the click told the downbeat as the others.
    `transcription.json`: each note's pitch, time, beat, bar, length, end, `voice` (its place in its chord from the
    top), `attack` and `technique` (picked, hammer, pull, slide); no string or fret, `tuning` null; `kind` and
    `pitch_range` (the range its notes were looked for in).
  - **The page:** `/api/setup` gives each instrument `staves` ("tab", "grand", "one"; `tab` stays, true for the
    first) and "strings" its `kinds`; a take its `staves`, `kind` and `kind_name`. One staff is drawn notation only,
    with no Notation/Tab switch ("One staff, the notes as they sound"); "Play the transcription" with the steel
    guitar's program (25).
  - **"Review it":** once dobro-transcription's `review/take_check.py` names "strings" in `INSTRUMENTS` (its
    branch for this, beside the pipeline's), as the piano's.
  - **Tried on the desktop** (fake device, the pipeline from dobro-transcription's branch, nice 19, two threads):
    the guitar's G scale take (dobro-transcription `exercises/guitar/002-g-major-scale`, 28 s) brought in by
    `tools/import-take --instrument strings --bpm 80 --click --downbeat 3.513`, transcribed in 1.4 s (30 notes, 8
    bars, G2 to G4 and back as played; the dobro's path on the same take reads bars 4-5's G4s as G5); Red Wing's 80 s
    take by the generic path 2.2 s (the dobro's 2.9 s), so the first estimate stays the dobro's. "Review it" made
    `take-check-2026-10-19-172725` (two questions), drawn by the review page on one staff. Headless Chrome at 390 wide
    (the instrument step with the kinds, the take drawn on one staff) and 1180x820 (Ready: "Mandolin at 90 bpm"). The
    first system's third bar runs past the card's edge at 390 wide, for the dobro's take as for this one: alphaTab's
    page layout, as before. `tests/test_transcriber.py`: a take told its kind and one not sure, the refusals, and the
    real pipeline on the guitar take (with `DOBRO_TRANSCRIPTION` set to a checkout that has the generic path); the
    suite 147 passed. The pipeline's generic path checked under the unit's librosa 1.0.0 too (the same notes).
  - **Not deployed.** The order as the piano's: dobro-transcription's `review/tools/deploy` (copies `review/` and
    `pipeline/src/`, with `transcribe/strings/`; restarts the review app only; its idle check looks at 8000-8004,
    not this app's 8006, so this app idle by hand first); then `tools/deploy` here (every app here restarted, the
    unit's `/etc/take-transcriber.toml` kept: the installer notes it has no `[instruments.strings]`); then
    `[instruments.strings]` (`name = "Any stringed instrument"`, `input = 4`) added to `/etc/take-transcriber.toml`
    by hand and `sudo systemctl restart take-transcriber`. No new dependency: the generic path uses what the review
    venv has (numpy, scipy, librosa).
- **"Draft a chord chart"** (2026-10-20, branch `chart-draft`; the player: "a path for starting with a recording that
  goes through the pipeline to extract a chord chart, then that can get corrected / confirmed / reviewed";
  dobro-transcription's card `band-song-chart`). On a take's page, under "Review it", for any take with its recording
  (one brought in by `tools/import-take` too, transcribed or not): `transcriber/chart.py`.
  - **The dialog:** in the box, the song's title (the take's name to start with; may be left empty) and what is on
    the recording: Not sure, Bass, Guitar, Dobro, Piano, Voice, A band. The take's own instrument is chosen to start
    with where the drafter knows it (any stringed instrument's kind when it is a guitar, a dobro or a bass), else Not
    sure. It says the tempo and meter the drafter is told.
  - **The run,** in the background, one chart at a time: `cd ~/dobro-transcription/pipeline && PYTHONPATH=src
    ~/dobro-transcription/review/.venv/bin/python -m transcribe.chart_draft TAKE/take-01.flac TAKE/chart-draft --instrument I [--bpm N]
    [--meter N] [--title T]` (the pipeline's venv, nice 19, two threads, its own process group, held with SIGSTOP
    while this app records); then `make_review chart-check TAKE/chart-draft --install local --id
    chart-check-<take id>`, as "Review it" runs the take check (the title goes to the drafter only: the chart check
    names the review from the draft, "Check the chart: T"). `--bpm` is the tempo transcribed at, else the take's;
    `--meter` its beats in a bar. The id is the same each time, so a chart drafted again keeps the review's answers.
    A chart there was waits in `chart-draft.old/` and comes back if the draft fails. What both print goes to
    `TAKE/chart.log`; `meta.json` keeps `chart` (the review's id, when, the instrument, the title, the seconds), or
    the error with the review there was.
  - **The page:** "Drafting the chart…" then "Making the review…", the time (and "of about" once this unit has
    drafted one of that instrument), "Held while recording"; the rest of the app works meanwhile. Then "Open the
    chart check" (`http://<host>:8005/#/r/<id>`) and "Draft it again". `POST /api/takes/ID/chart` (`instrument`,
    `title`), `GET /api/setup`'s `chart` (available, why, the instruments), `/api/status`'s `chart`. A take is not
    deleted while its chart is drafted. The home tile says "Drafting a chord chart".
  - **Until the unit has both** (`pipeline/src/transcribe/chart_draft.py`, and `chart-check` among make_review.py's
    kinds; looked for each time the page asks), the box says which is missing and the button waits.
  - **Tried on the desktop:** `tests/test_transcriber.py` with stand-ins for the drafter and make_review (the commands,
    their folders and paths, the id, a failed draft and a failed review, one at a time, held while recording: its
    42 tests passed, the whole suite 249); the page in headless Chromium (muted) at 390x844, 820x1180 and 1180x820 against the stand-ins:
    the dialog, drafting, another take's button waiting, the link; no sideways scroll. Not with the real drafter
    (being written beside this). Not deployed.
- **The score inside its card at a phone's width** (2026-10-20, branch `phone-width`; dobro-transcription's card
  `transcribe-phone-width`, from the note of 2026-10-19 above: "the first system's third bar runs past the card's
  edge at 390 wide").
  - **Why:** the page kept the take's cursor (`.at-bar`, `.at-cursor`) inside alphaTab's `.at-surface`, and alphaTab
    puts each row of a render into the surface's children by their order. A render with more rows than the one the
    cursor was added after (the window narrower: a phone or an iPad turned upright) put two rows into the cursor's
    elements, drawn where the cursor was, on the bar last played: past the card's edge. Not seen on a page drawn once
    at 390 wide, only after a turn or another render.
  - **Also:** alphaTab's own resize lays its bars out again with the margin and bar widths of its last full render,
    and skips a render while the score is hidden. The page set the new scale even with the score put away (another
    screen), which the next resize would have laid out with the old bars. Not seen on its own in these runs.
  - **The fix,** in `transcriber/static/index.html`: the cursor beside alphaTab's drawing in the score's host, placed
    by the surface's offset. The score drawn again in full whenever the card's width or the scale for it is not what
    it was drawn at, and only while it is shown (on a resize, a `ResizeObserver` on the score, and when the take's
    screen is drawn again). After each render every bar and every drawn row is checked against the card: a bar too
    wide for the card on its own row makes the score smaller, down to 0.4 (not reached on these takes); a row drawn
    past the edge is drawn again in full, at most four times. Under 600 px wide, alphaTab's margin is 12 px at the
    sides and 24 at the top and bottom (its default 35 all round): the music has 332 px of a 390-wide phone's card,
    not 286. The iPad's scale and margin are as before.
  - **Measured** in headless Chromium (Playwright, muted, the fake device) on six takes brought in by
    `tools/import-take` and transcribed: Red Wing (the dobro, notation and tab, and tab alone), Tennessee Waltz (the
    bass, both views), piano exercises 011 and 015 (grand staves), the guitar's Cherokee Shuffle and its G major
    scale (any stringed instrument). Drawn, a bar tapped, then turned from 844x390 to 390x844: 2 of the 8 drew past
    the card's edge before (222 and 292 px), none after; the same drawn at 1180x820, the takes list, 390x844 and the
    take again: 2 of 8 before (222, 292), none after; an iPad turned from 1180x820 to 820x1180: 5 of 8 before (33 to
    331 px), none after. Drawn once at 390x844: none before or after, the bars 36 px inside the card's edge before and
    13 after, so fewer rows: Red Wing 23 to 18 (notation and tab) and 21 to 17 (tab), Tennessee Waltz 22 to 19 and 21
    to 17, Cherokee Shuffle 46 to 36, the G scale 4 to 3, exercise 015 3 to 2, 011 2 as before. 1180x820 and
    820x1180: the same rows before and after. Playwright's WebKit at 390x844 (media muted), drawn once and turned:
    none past the edge. No sideways scroll, no console errors; every tap (the 8 cases in each of 7 runs) played the
    take from its bar within 0.12 s, with the cursor in that bar.
  - **The test:** `tests/test_transcribe_page.py` (`tests/transcribe_page.cjs`) on a made-up score (24 bars of eighth
    notes on one staff, silent sound): a phone turned both ways and an iPad turned upright. Against the page before
    it fails (124 and 152 px past the edge, the score drawn in the cursor); it passes after. Skipped without node's
    Playwright and ffmpeg. `tests/test_transcriber.py` 42 passed, the whole suite 258 (Playwright found). Not
    deployed.

### 9. The board (started 2026-10-19)

The player's request: "we're starting to get more and more threads in this project going, so i think we need to
start maintaining a project management board. i want basically kanban where i can see a list of cards organized by
which part of the project they relate to, how important they are, how much work they are for whom, and who needs to
do them ... so i can easily figure out where to focus depending on whether i want to do musical things or do software
things." Then, the same day: "i want the kanban to be organized into the kind of work it is for me: listening and
evaluating, playing an instrument and/or singing, managing an AI agent, communicating with other people, or working
with the hardware".

- **An eighth program on the control unit:** `board/`, port 8007, `setup/room-board.service`, its own config
  (`/etc/room-board.toml` from `config/board.toml`) and data (`~/board-data`). It holds no audio device, is in no
  peers list and answers no `/api/yield`. A tile on the home page.
- **The cards are dobro-transcription's** `board/cards.toml` (its `board/README.md`: the fields, and `board.py`).
  `python3 board/board.py json | tools/push-board` puts them on the unit as `cards.json` (written as
  `cards.json.new`, then renamed; checked first: version 1, unique ids, each status, work, importance, who, area and
  effort as the contract has them). Nothing restarts.
- **The player's edits** are kept on the unit as a log, `changes.jsonl`, one JSON object a line, appended in one
  write under a lock (`flock` on `changes.lock`; a line cut short before is left on its own line):
  - `{"id", "at", "by": "player", "card", "set": {...}}`: status, importance, who, work, effort_player,
    effort_claude, waiting_on, area. A set that changes nothing is not written.
  - `{"id", "at", "by": "player", "new": {<every field of a card>}}`: from Add, its id a slug of the title (cut at a
    word, 40 characters at most) and four hex digits; Backlog, importance 2, the work and who as chosen.
  - `{"id", "at", "by": "player", "card", "note"}`. board.py adds a note to the card's details under "Notes:".
  The app shows the base with every change not in its `applied` list laid over it, in order, so an edit shows at
  once; a card with edits still in the log says "Not synced". A change for a card not there, or with a value the
  contract does not have, is left out of the view (and stays in the log).
- **Back to the desktop:** `tools/pull-board` reads the log on the unit (`python -m board.store pending`, under the
  lock, whole lines), runs `board.py apply FILE` and `board.py fmt` in dobro-transcription, then `board.py json |
  tools/push-board`, and only once that push has succeeded moves the changes in the new base's `applied` from
  `changes.jsonl` to `changes.applied.jsonl` on the unit (`python -m board.store archive`). An edit made during the
  sync, or one apply refused (a card deleted on the desktop, say), stays pending; the tool says which and exits 1.
  If fmt, json or the push fails, nothing is archived and the next pull takes up from there (apply takes each
  change once, by `applied.txt`). `cards.toml` and `applied.txt` are left to commit.
- **Live cards** (shown, never stored), read every 10 s at most (`/api/status` uses the last read and refreshes in
  the background, so the home tile never waits): the review app's open reviews (`/api/reviews`: not demos, not
  all answered; Listen & evaluate), and the exercise app's "to record", counted as its board counts them (wanted
  again or no take yet, not recorded here since), one card for every instrument (Play & sing), linking to its board
  and each instrument's list. A review that a card links to (`#/r/<id>`) and that is not done is shown on that card
  instead ("Live · 6 questions to answer"): on 2026-10-19 all three open reviews had a card. An app that does not
  answer within 1.5 s has no cards.
- **The page, iPad first,** in the room's look (`--faint` for lines only):
  - **What kind of work:** Listen & evaluate, Play & sing, Manage the agent, People, Hardware, or All, each with its
    glyph (headphones, a note, a robot's head, two people, a plug, four squares), its words and its count of open
    cards. In All, each card says its work too.
  - **The columns:** Backlog, Next, Doing, Waiting, Done (the last two weeks; "+ 3 older" shows the rest). On its side
    every column is on the screen; upright three, and the board scrolls a column at a time (a row of the columns
    with their counts to jump); a phone one column. Area as a chip on each card, or as lanes across the columns
    (on a phone, the areas as headings in the column).
  - **A card:** its title, importance in bars and words (High with a purple edge), its work (in All) and area, who
    (You, Claude, Others), the efforts ("you 30m · Claude M"), blocked by (red, with a mark) or waiting on (amber,
    with a clock), links, and "Not synced".
  - **Its sheet:** move to a column, "Claude: do this next" (Next, Claude added to who, importance 1: Claude's
    queue; the first action on the agent's cards), importance, who; folded: work, effort, area, waiting on (shown
    open while waiting). The details as Markdown (escaped; only http(s) links are links), what blocks it and what it
    blocks (each opens), the links (URLs open; repo paths are shown as paths on the desktop), notes and a new one,
    the source and dates. A live card's sheet has its link and nothing to change.
  - **Moving cards:** drag between columns with a mouse; on a touch screen the sheet's buttons (no drag).
  - **Add:** a title, an area, who and the work (the view's, else the last chosen); into Backlog.
  - **Filters** (area, importance, who, your time, Claude's size) and the grouping, with the view, kept per device
    (localStorage, read and written in try/catch).
  - **Accessibility:** text at 5.7:1 or more (white on the purple fill; ink3 #A79FB4 6.0:1 on cards, 7.4:1 on the
    background, 5.9:1 on a disabled button), targets 44 px, a label for every control, and nothing told by colour alone (importance,
    work and who in words, a pressed choice with a check).
- **The home tile:** what is in Next or Doing for each kind of work, the first three that have any ("4 to listen · 3
  to play · 14 for the agent"; `by_work` has all five), else "Nothing in Next or Doing"; "No cards yet" before the
  first push.
- **The goals** (2026-10-20, the player: "our board needs something to represent the large-scale goals and our
  progress towards them, and which cards are related to each. like, maybe a chart for each goal showing progress over
  time ... and the color for each chart matches the color of cards that are part of that goal. (a card could have
  multiple colors if it's relevant to more than one big goal)"). The goals are dobro-transcription's too, in
  `board/cards.toml`; `board.py json` adds them (its `board/README.md` has the contract):
  - `"goals": [{"id", "name", "about", "release", "shipped", "color", "history", "notes"}]`: `release` says what
    release-ready (9) means for it, `shipped` what released (10) means (may be empty); `color` one of ten (below);
    `history` `[{"date", "stage", "note"}]`, oldest first, the last the stage now; `notes` `[{"date", "text", "by"}]`,
    `by` "player" or "claude". Each card has `"goals"`: the goals it serves, the most relevant first (may be `[]`).
  - **The stages,** ten, as whole numbers: 1 Idea, 2 Idea with a plan, 3 Testable prototype, 4 Tested prototype,
    5 Rough but usable, 6 Decent, 7 Getting pretty good, 8 Almost release-ready, 9 Release-ready, 10 Released. The
    JSON's `"stages"` (the ten labels) are used when it has all ten, the board's own copy otherwise. Shown as the
    number and the label ("6 · Decent") and a ten-step meter beside them.
  - **The log gains** `{"id", "at", "by", "goal", "stage": <1-10>, "note"}` (a goal moved; the note may be empty; a
    move to the stage it is at is not written) and `{"id", "at", "by", "goal", "note"}`; a card's `set` may carry
    `goals`. They are laid over the base as the cards' are, and a goal with edits in the log says "Not synced".
    pull-board gives them to `board.py apply` as written and counts them ("1 goal stage, 1 goal note").
  - **The Goals view,** a seventh beside Listen ... All (with the goals there; upright on an iPad the seven are named
    by their short words). A view and not a strip above the board: on an iPad a strip of six charts would each be
    about 180 px wide on its side and 130 upright, too small for ten levels and for 44 px dots, and would take the
    board's height in every view; a view gives each goal a tile of 300 px or more (three across on its side, two
    upright, one on a phone). Each tile: its name, the stage (meter, number, label), the chart, the release line
    (two lines at most), its open and done cards; a tap opens its page.
  - **The chart** (inline SVG, drawn at the width it has, again on a resize): stages 1 to 10 up the side, the odd ones
    numbered (in a sheet 520 px wide or more, each with its label), the dates from the first entry to today along the
    bottom (and one between where there is room), a step line in the goal's colour, a dot for each change (hollow
    while not synced). A dot is a 44 px button; dots nearer than that share one. A tap shows its date, stage and note
    under the chart, with the change before and after; the chart's text alternative lists every change.
  - **A goal's page** (2026-10-20, the player: "when i click on a goal, i'd like the board to be shown below it, but
    filtered to the cards that are part of that goal"), from its tile, a goal's chip on a card, or a goal in a card's
    sheet. Its link is `#goal=<id>`, so a reload or the back button keeps the place; a link to a goal not on the board
    shows the board and says so.
    - **At the top, the goal:** "‹ All goals" (the Goals view, by the back button's way when the pages were opened
      here), its name, the stage, what release-ready (9) and released (10) mean (a line each on an iPad on its side,
      two upright; whole in its notes), "Move to stage…", "Add a note", "History and notes", and the chart beside them
      (128 px tall on an iPad). It opens with no dot chosen. Measured on the fixture: 196 px of goal over 463 of board
      at 1180x820, 202 over 761 at 820x1180. On a phone the chart and actions fold ("Its chart, a move, a note"; the
      fold kept per device), the goal then 155 px.
    - **Under it, the board:** the five columns with that goal's cards only, the same cards, sheets, moves and drags.
      The kinds of work choose within the goal, their counts its cards'; Goals is not among them there (All goals is the
      way). The other filters and the grouping as on the board; the goal filter not (the page is its own). Add there
      makes a card for the goal (the new card, then its goals: two edits in the log).
    - **Its sheets:** "Move to stage…": the ten, the stage now marked; choosing one asks for a note, then "Move to 7 ·
      Getting pretty good", and the sheet closes, the page saying the move ("not synced", the new dot hollow). "Add a
      note" and "History and notes": what it is, what release-ready and released mean, the note field (ready, for Add a
      note), the notes (who wrote each, "You" or "Claude", as a badge) and the history, newest first.
  - **The cards wear their goals:** a 5 px stripe across the top, a segment for each goal in its order (2 px apart),
    and a chip naming each (outlined in its colour; a long name cut short with "…"). With goals, the areas' dots are
    grey: colour is the goals'. A tap on a chip opens that goal's page (on that goal's own page, the card). The card's
    sheet lists its goals (each opens its page, its stage beside it) and "Goals…" chooses them, numbered in the order
    chosen.
  - **A goal filter** (multi-select, "No goal" too), kept per device with the others; it shows the cards with any of
    those chosen.
  - **The home tile:** `/api/status` adds `"goals": [{"id", "name", "stage", "stage_name"}]`; the tile's words are as
    before.
  - **The colours:** each a mark, never text (the goal is named beside it). Measured (WCAG contrast; OKLab ΔE ×100 to
    the nearest of the other nine, and to the nearest of the app's purple key #7C3AED/#8B5CF6/#A78BFA/#B9A0FF, red
    #FF8F85, amber #E8B93B and live green #4ADE80):

    | name | hex | on cards #28232F | on sheets #1E1A24 | on the background, and #121016 on it | nearest goal colour | nearest of the app's |
    |---|---|---|---|---|---|---|
    | teal | #5CE3E0 | 9.9 | 11.0 | 12.2 | sky 11.5 | live green 12.9 |
    | orange | #E07A12 | 5.1 | 5.7 | 6.3 | red 12.5 | red 12.0 |
    | pink | #FF73C3 | 6.2 | 6.9 | 7.6 | violet 13.5 | red 12.2 |
    | green | #55B46E | 5.9 | 6.6 | 7.3 | teal 17.5 | live green 11.5 |
    | gold | #FFEB8A | 12.8 | 14.3 | 15.8 | lime 11.6 | amber 13.4 |
    | blue | #2F8CFF | 4.6 | 5.1 | 5.7 | sky 15.0 | purple 13.2 |
    | red | #E0505E | 4.0 | 4.5 | 4.9 | orange 12.5 | red 14.7 |
    | violet | #D04BDB | 4.2 | 4.7 | 5.1 | pink 13.5 | purple 13.2 |
    | lime | #C6EE2A | 11.4 | 12.8 | 14.1 | gold 11.6 | live green 13.6 |
    | sky | #4CC2FF | 7.6 | 8.5 | 9.4 | teal 11.5 | purple ink 13.6 |

    Every one clears 3:1 as a mark and 4.5:1 for dark text set on it. Ten hues cannot all be 15 apart (the chart
    method's floor for telling two apart at a glance); the closest pairs are teal and sky (11.5), gold and lime
    (11.6), orange and red (12.5). The six goals of 2026-10-20 (gold, teal, orange, red, violet, sky) have teal and sky
    and orange and red among them; protan and deutan views keep every pair of those six at 8.7 or more.
- **Tried on the desktop:** the tests (52 new; the suite 204 passed): the merge, the appends (80 notes from eight
  threads through four stores, whole lines), the refusals, the archive, the live cards with the apps up, down and
  slow (the status in under 0.5 s); push-board and pull-board locally and through a stand-in ssh laid out as the
  unit, with a stand-in board.py (refusals, failures at each step, a retry) and with dobro-transcription's own
  board.py on a copy of its 127 cards (three edits made in the app, pulled, applied, pushed, archived). Headless
  Chrome via Playwright (`tests/board_page.cjs`, run by `tests/test_board_page.py` when node finds Playwright, or
  `PLAYWRIGHT_NODE_PATH`) at 1180x820, 820x1180 and 390x844, on a fixture of 46 cards: each view and its count,
  a card opened and moved with the buttons, a note, "Claude: do this next" on two cards, a card added, filters and
  lanes kept over a reload, Done's fold, a drag (with a mouse); no console errors, no sideways scroll, no target
  under 44 px. The real 127 cards with the unit's live apps at the three sizes: no console errors, nothing wider
  than its card or the screen. Not deployed.
  - **The goals' tests** (2026-10-20; the suite 247 passed, 1 skipped): the goals laid over the base (stage moves,
    notes, a card's goals; a stage of 11, "7", true, a goal not there, left out; one the base has already, not
    twice), the appends and their refusals (422, 404, 409), a goal's colour when it has none, the JSON's stage labels
    and the board's, a board with no goals as before; push-board's checks of the goals (14 ways to be wrong; two goals
    one colour only a warning); pull-board passing the goals' edits to a stand-in board.py (locally and through the
    stand-in ssh) as written, and a goal apply refuses; a round trip with dobro-transcription's own board.py once its
    JSON has goals (skipped until then). Headless Chrome (muted, `--mute-audio`) at the three sizes on
    `tests/fixtures/board-goals.json` (the 46 cards with six goals, 1 to 7 changes each; cards with no goal, one, two
    and three): the stripes (one segment, two equal ones in order, none), the chips, the Goals view and a chart (the
    axis, the text alternative, three changes on one day sharing a button), a dot's readout and its steps, a goal's
    sheet (its release lines, its cards by status, history, notes and who wrote them, the ten stages), a move with a
    note, a note, from a goal to a card and back, a card's goals chosen in order, the goal filter kept over a
    reload, "No goal", Add from the Goals view, the stages' labels; and on the cards with no goals, nothing of them
    anywhere and the areas' colours as before. No console errors, no sideways scroll, no target under 44 px.
  - **The goal's page's tests** (2026-10-20; the suite 248 passed, dobro-transcription's board.py with its goals now
    there for the round trip): at the three sizes, a goal's page from its tile (its link, the goal at the top no taller
    than 215 px with the board at least half the screen on an iPad, 170 folded on a phone), its board with only that
    goal's cards in every column and every column's count, the kinds of work counted within it and Play chosen, a dot,
    a card's sheet and a move there, a chip of the goal itself opening the card, "Move to stage…" (the ten, a note, the
    sheet closed, the page saying it), "Add a note", "History and notes", Add for the goal, "‹ All goals"; from a
    card's chip to another goal's page, the back and forward buttons, a card's goal in its sheet, a link and a reload on
    `#goal=…`, no goal filter there, All goals after a link, and a link to a goal not there.
- **To deploy** (everything idle; the board takes nothing from the others): add the tile to `/etc/room-home.toml`
  by hand (`config/home.toml`'s `[[apps]] id = "board"`, a `.pre-board.bak` copy first), then `tools/deploy` (it
  installs `/etc/room-board.toml`, makes `~/board-data`, installs and starts `room-board`, and restarts every app
  here), then from dobro-transcription `python3 board/board.py json | ~/play/piano-recorder/tools/push-board`, then
  the home page's Board tile ("4 to listen · ...") and `http://practice.local:8007/`.
- **To deploy the goals** (not deployed; only when the player says): no new config, service or dependency.
  1. dobro-transcription's goals first: `board.py` with the goals and their ops in `apply`, its tests passing, and
     `python3 board/board.py json` carrying `"goals"` and `"stages"`. Then the skipped round trip here runs
     (`tests/test_board_tools.py::test_the_real_board_py_takes_the_goals_edits`): run the suite.
  2. `tools/pull-board`, so no edit is left in the log across the change (the new app reads the old edits too).
  3. The unit idle: `tools/deploy` restarts every app here (both recorders, the exercise, chord and transcribe apps,
     the home page and the board), so nothing recording, playing or transcribing, and nobody at the board.
  4. `tools/deploy`.
  5. From dobro-transcription, `python3 board/board.py json | ~/play/piano-recorder/tools/push-board` ("... cards in
     ... areas, 6 goals"). Either order is safe: the app before this push shows the board as before, and an old app
     ignores a push's goals.
  6. `http://practice.local:8007/`: the Goals view and a card's stripes; `curl -s
     http://practice.local:8007/api/status` has `goals`; the home page's Board tile as before.

### 10. The room's inputs (started 2026-10-20)

The player, 2026-10-20, with a new mic (a Blue Bluebird SL) plugged in: each app kept only the inputs its own config
listed (`keep_channels`: the exercise and transcribe apps inputs 3, 4 and 7, the chord app 3, 4 and 1, the practice
recorder the profile's 3 and 4), so a mic on any other input reached the unit and was thrown away; and input 3 was
still "Nadine" in every sidecar with the Bluebird on it ("the mxl and new mic (still labeled nadine)").
dobro-transcription's card `the-music-room-apps-record-any-or`.

- **One file for the room:** `~/room-inputs.toml` (`recorder/inputs.py`; an app config's `inputs_file`, and
  `$ROOM_INPUTS_FILE` over it, may name another). An `[inputs.N]` per input, by the interface's number: `name`,
  `details`, `for` (a pickup or mic on one kind of instrument; none: the room's), `record`, and `since` (when a page
  last changed it). Every app reads it again when it changes (one stat a call), so a mic named in one app is named
  in all of them, from their next take. `config/inputs.toml` is its first copy, installed once by
  `setup/install.sh` (as wired on 2026-10-17: 3 the Nadine, 4 the MXL 990, 7 the piezo, all recorded; 5 and 6 the
  record player's, named and not recorded); after that the pages keep it. An input with no table is not recorded.
- **The device's inputs are the profile's:** `[capture] inputs` (the 18i8: 8, input N on capture channel N - 1) and
  `sockets` ("Front, mic, line or instrument" for 1-2, "Front, mic or line" for 3-4, "Rear, line" for 5-8). The
  fake profile has four, the BMG-04F one (its main mix).
- **Every app records the room's recorded inputs,** in input-number order: the practice recorder, the exercise app,
  the transcribe app (take-01.flac the instrument's input alone, raw/ all of them) and the chord app (what it hears
  and keeps in its clips). Every take's sidecar has `channels`: `{"channel", "input", "input_name",
  "input_details", "input_for"}` per channel (the exercise app's with `carries` too, as before; the chord app's
  keeps `name` as well), written when the take starts and again at its end, so a mic named while a take is recorded
  is named in it. A transcribe app take's `meta.json` has them too (what raw/ holds), and its take-01.flac is cut from
  the channel the sidecar says carried its input. With nothing recorded a take is refused ("no input is set to be
  recorded", 409).
- **Every input's level, recorded or not:** the capture reads all 20 channels as before; each block's peak per
  input goes in the status as `input_levels` and `input_peaks` (by input number), while recording and while
  listening between takes, with `inputs_version` (raised when the file changes: the pages read `/api/inputs`
  again). The pages' meters are keyed by input number now, not by channel.
- **The pages: one script for all four,** `recorder/static/inputs.js` (each app serves it at `/inputs.js`, with
  `GET /api/inputs`, every input of the device, and `PUT /api/inputs/N`, `{"name", "details", "for", "record"}`).
  On each record screen: the recorded inputs, named, each with its meter (the practice recorder's record panel in
  place of its one meter: "Records", "Recording" while it does); an input not recorded and not named that hears
  something (its peak over -70 dBFS: the 18i8's empty inputs read -110 to -112 dBFS rms, the MXL in a quiet room -67
  with peaks of -52) says so, with "Record it"; and "Inputs…" opens the sheet. The sheet lists all of the device's
  inputs with live meters (it asks the app to listen while open): for each, Recorded or Not recorded, Edit, and "A
  new mic here" (a blank name, what it is, and what it hears: the room or one instrument); an input with nothing named
  has "Name it", and naming it records it. The look is the apps' own: dark, purple for what to tap. The exercise
  app's step 1 and Ready, the transcribe app's step 1, Ready and record screens, and the chord app's chips have the
  same lines; the transcribe app also says what else a take keeps ("Also kept: In 4 · Room mic"). The practice
  recorder listens to its inputs only while its sheet is open or a take records (it never listened between takes).
- **The transcribe app's instruments by name:** `[instruments.*] input = "Room mic"` (the dobro, the piano, any
  stringed instrument), `"Nadine"` (the bass), found among the recorded inputs wherever that mic is plugged in;
  with no recorded input of that name, one `for` the instrument's kind (the bass: the piezo), else the first room
  mic (`Inputs.pick`, its `found` in `/api/setup`; the page says "No recorded input is called Nadine: the usual is
  the one for this instrument, In 7 · Piezo"). The page keeps its own choice for an instrument by the input's name.
  `/etc/take-transcriber.toml` as installed before (`input = 3`) reads as the name its own `[inputs.3]` gave that
  input ("Nadine"), so the unit needs no edit; a number alone is used only where no mic is named on that input.
- **The tools read a take's channels by name:** `recorder/sidecar.py` (`find_channel`: by an input's name, any case,
  or its number, "In 4"; or by what it carried); `python -m recorder.sidecar TAKE.wav` lists them, `--input NAME
  --out X.wav` writes that one alone. `tools/pull-take` prints each channel's input after copying, and `--input NAME`
  also writes `TAKE_ID.<name>.wav`. `tools/import-take --input NAME` takes FILE's channel that carried NAME, from the
  sidecar beside it (`--channel N`, by place, as before), and its meta.json names the input. dobro-transcription's
  `exercises/bring_in.py` has the same (`--input`), with its own copy of the reader.
- **Before the file, and old sidecars:** while `~/room-inputs.toml` does not exist (an app's first start, the
  tests), an app's own config stands in as before: its `keep_channels` (else the profile's), in that order, named by
  its `[inputs.N]`; the first change from a page writes the file from that. The `/etc` configs' `keep_channels` and
  `[inputs.N]` are not read once the file exists (install.sh says so). Sidecars of before 2026-10-20 read as they are:
  the exercise app's had `channels` already, the chord app's named each `name`, the practice recorder's had none (a
  channel then only by its place).
- **Tried on the desktop** (the fake device, its four inputs; 2026-10-20): `tests/test_inputs.py` (16: the file
  and the config standing in, writing it, another app's write read, bad tables left out and said, `pick`, a take's
  sidecar and a name given mid-take, a take refused with nothing recorded, the monitor hearing every input, a name
  from the practice recorder in the exercise and chord apps, an exercise take's channels, the chord listener
  following the file, the transcribe app's instruments by name and by number before, the bass following the
  Nadine to another input, `find_channel` on new and old sidecars, the sidecar tool, pull-take's options), an
  importer test (by name, the sidecar's names kept, a name not there, no sidecar), and
  `tests/test_inputs_page.py` (headless Chromium, muted, the four apps on one file: the practice recorder at
  390x844 names the recorded inputs, its sheet lists all four with input 1's meter moving, input 2 "hears something"
  and is named from "Record it"; the exercise app at 1180x820 has the name, a new mic on input 3 is named from its
  sheet and the practice page shows it by itself within a few seconds; a practice take keeps the four inputs, each
  named; the transcribe app's bass falls back to the piezo and says so; the chord app's chips; no console errors, no
  sideways scroll, the sheet inside the screen). Not deployed.
- **To deploy** (only with the player's go-ahead, every app idle; it restarts every app here): `tools/deploy` (it
  installs `~/room-inputs.toml` from `config/inputs.toml`, and says that the `/etc` configs' inputs are no longer
  read). The takes' channels change from the next take on: the practice recorder gains input 7, the chord app drops
  input 1, every take's channels are in input-number order. Then on any app's page: Inputs…, input 3, "A new mic
  here": "Bluebird SL", what it is, the room; Save. The bass's usual input is then the piezo until the Nadine is
  plugged in again and named "Nadine", on whichever input. dobro-transcription needs nothing on the unit for this.
- **Left:** phantom power is still the profile's (on for 3-4, off for 1-2): a condenser named on input 1 or 2 hears
  nothing until it is switched there (dobro-transcription's card `inputs-phantom-from-the-page`).

## Open questions

- ~~Which instrument?~~ The piano (2026-10-10). The speakers are across
  the room, with the piano between. Mic placement at the piano is still
  open. The mixer's USB is mono, so the pair of SDCs gives no stereo
  through it.
- Is there Ethernet in the mixer's room, or Wi-Fi only?
- What are the speakers, and how are they connected?
- Is there a spare USB DAC or USB sound card anywhere (for option B under
  "Speakers while recording")?

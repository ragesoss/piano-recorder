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
    `/etc/exercise-recorder.toml.pre-inputs.bak`). Not yet tried on the LIVA: the practice
    recorder recording while the exercise page is open (the inputs handed over on its yield).
- **Next:** a tool to bring exercise takes into dobro-transcription with
  their `takes.csv` rows (`tools/pull-take` reads only the practice
  recorder's takes; exercise takes need `/etc/exercise-recorder.toml`),
  which could also take the backing out on the desktop instead of the Pi.

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
- **Deployed 2026-10-16, about 07:10** (both recorders idle). The installer added
  `/etc/chord-listener.toml` and the unit; by hand, the peers line of `/etc/practice-recorder.toml` and
  `/etc/exercise-recorder.toml` and the tile in `/etc/room-home.toml` (copies kept as
  `*.pre-chords.bak`). Not yet tried on the LIVA: listening to the 18i8, or a recording taking the
  inputs from it.

## Open questions

- ~~Which instrument?~~ The piano (2026-10-10). The speakers are across
  the room, with the piano between. Mic placement at the piano is still
  open. The mixer's USB is mono, so the pair of SDCs gives no stereo
  through it.
- Is there Ethernet in the mixer's room, or Wi-Fi only?
- What are the speakers, and how are they connected?
- Is there a spare USB DAC or USB sound card anywhere (for option B under
  "Speakers while recording")?

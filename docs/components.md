# Components

The system is a chain of swappable parts. Each part fills a **slot**. A slot
has an interface: what the rest of the system relies on. Swapping a part
means:
- checking that the new part meets the interface, or writing down how it
  differs;
- changing the config;
- running that slot's tests.

The software reaches hardware only through **profiles** and the
**config** (see `PLAN.md`, "Design"). Nothing else in the code names a
sound card, a mixer control or an address.

## The chain

```
SDC x2 ──XLR──▶ BMG-04F ──USB, A-to-A──▶ Pi 3 ◀──home network──▶ phone
                 │    ▲                    │
                 │    └── USB playback ────┘
                 └── an output (which: to check) ──▶ speakers
```

Today one box, the BMG-04F, fills two slots: the capture interface and
the playback output. They are kept as separate slots because a later swap
may split them (see "Interactions").

## Summary

Status: **verified** means measured on the real hardware, with the date.
**Planned** means chosen but not set up. **Unknown** means not checked
yet.

| slot | current part | status | software touchpoint | tests on swap |
|---|---|---|---|---|
| 1. Microphones | 2 small-diaphragm condensers (SDCs), XLR, at the piano | verified through the BMG-04F, 2026-10-10 (laptop); moved closer 2026-10-11, T5 due | none | T5, T8 |
| 2. Capture interface | Focusrite Scarlett 18i8 3rd Gen, USB (from 2026-10-14; the BOMGE BMG-04F before) | T1-T3 by hand, 2026-10-14; T4, T5, T7-T9, T11 due | capture profile (`scarlett-18i8-gen3`); `capture` in the configs | T1-T5, T7-T9, T11 |
| 3. Playback output | the 18i8's USB playback (PCM 1-2) into its internal mix, to its monitor outputs | T1-T3 by hand, 2026-10-14; records through it heard (player); T6-T9, T11 due | playback profile; `playback` in the configs; the ambient synth's `device` | T1-T3, T6-T9, T11 |
| 4. Speakers | JBL 3 Series MkII pair, on the 18i8's monitor outputs 1-2 (from 2026-10-14) | records heard through the 18i8 (2026-10-14); T6, T8 due | none | T6, T8 |
| 5. Control unit | Raspberry Pi 3 Model B v1.2 (the LIVA as the alternate), host `practice` | running the recorder service; T9 pass (clean), 2026-10-10 | host config; the setup script | T1-T4, T6, T9-T11 |
| 6. Storage | the Pi's SD card (Transcend, 32 GB) | planned | `takes_dir` in the config | T9 |
| 7. Network | home LAN and router; the Pi on the home Wi-Fi, at its reserved address | Wi-Fi verified 2026-10-10; address reserved | `address` in the config; a DHCP reservation | T10, T11 |
| 8. Client | the player's Android phone, a browser | planned | none (the web page) | T11 |

## The slots

### 1. Microphones

- **Role:** turn the instrument into an analog signal.
- **Interface:** XLR into the capture interface. Phantom power if it is a
  condenser.
- **Current:** two SDCs, with XLR cables, bought on Temu (the player, 2026-10-16). They sounded the
  same, so either will do. Through the 18i8 the room is their noise floor, and the piano detector and
  the chord app's checks were built on one of them: cheap, but measured and good enough here, so a
  cheap mic is worth a trial if it passes the same measurements (room tone at the same gain, a
  spectrum against these on one source, the triads take again).
  - Phantom (+48 V) must be on. With it off, the mixer got nothing.
  - Through the BMG-04F at channel gain 0 (the knob's middle), hard dobro
    picks at about 20 cm peaked at -15 to -18 dBFS, over a room tone of
    -74.8 dBFS (most of it below 30 Hz).
  - The capture interface sums both mics into one mono track (see slot 2).
  - 2026-10-11: the player moved the mics closer to the piano, for more
    signal over the room and the mixer's hiss (slot 2, "Noise on
    playback"). The new distance is not noted. T5 is due again: hard
    playing must still peak under -6 dBFS.
- **Also at hand:** an Ear Trumpet Labs Nadine (upright-bass mic). It
  works through the mixer, about 10 dB quieter than an SDC.
  - It needs phantom power (the player, 2026-10-12). Mounted on the bass's
    afterlength, on channel 2 at GAIN maximum (2026-10-13): peaks -25 to
    -31 dBFS, the loudest 50 ms -39 to -43, floor -82. See PLAN.md step 5.
- **Wanted, when a good used one turns up locally (not a priority):** a
  room mic, for what a listener hears (the instruments and the room
  together), beside the close mics and pickups. It needs its own input:
  input 2 of an 18i8 (`replacing-the-mixer.md`); on the BMG-04F there is
  none to spare.
  - **What to look for:** a large-diaphragm condenser, ideally switchable
    patterns (omni for the room; cardioid to turn its back on the air
    conditioner). Self-noise 15 dB(A) or less. XLR with phantom power: no
    USB mics, no ribbons (low output, fragile).
  - **Models often seen used** (rough used prices, US, 2026; check local
    listings): Audio-Technica AT4040 (cardioid) $175-250, AT4050
    (multi-pattern) $350-450; AKG C214 (cardioid) $225-300; Rode NT2-A
    (multi-pattern) $200-275; AKG C414 XLS/XLII (multi-pattern) $500-750;
    Neumann TLM 102 $450-600, TLM 103 $700-900.
  - **Checking a used one:** hear it before paying (no crackle, no hiss,
    steady output when the body is tapped gently); no dents in the grille
    (a drop can damage the capsule); try the pattern, pad and low-cut
    switches; for C414s and Neumanns, check the serial number with the
    maker (fakes exist). A shock mount or case with it is a good sign.
  - **When added:** no software change; T5 and T8, then a line in the swap
    log.
- **Swap:**
  - another XLR mic: no software change; run T5 and T8.
  - a USB mic: that is a new capture interface, not a mic (slot 2).

### 2. Capture interface

- **Role:** mic preamp, phantom power, and converting to digital audio over
  USB.
- **Current (2026-10-14): Focusrite Scarlett 18i8 3rd Gen** (`hardware/scarlett-18i8-gen3.toml`),
  bought by the player for the music room, on the control unit's USB. Probed by hand (T1-T3):
  - USB id `1235:8214`, ALSA card `USB`, high-speed USB; the kernel's scarlett2 driver
    ("Scarlett Gen 3 Mixer Driver enabled"), Linux 6.18.
  - Capture: S32_LE (24 bits), 44.1/48 kHz with 20 channels, 88.2/96 kHz with 16.
    PCM 01-08 carry inputs 1-8 (the factory routing, pinned in the profile), 09-10 S/PDIF,
    11-12 the Pi's playback (PCM 1-2), 13-20 ADAT.
  - Inputs: 1-4 mic preamps (combo XLR, gain on the knobs; 1-2 also instrument), 5-8 line
    (1/4", rear). Phantom power in pairs (1-2, 3-4), software controls; it comes back off
    after a power cut (persistence off), so the profile sets it when a capture starts. Air
    and pad per input, software.
  - Wired 2026-10-14: the Nadine on input 3, one condenser on the piano on input 4
    (phantom 3-4 on); kept channels 3 and 4. To change with more XLR cables.
  - Standalone mode on: the unit keeps its routing with the Pi off.
- **Before: the BOMGE BMG-04F** (`hardware/bmg-04f.toml`), described below. Off the Pi's USB
  from 2026-10-14; until RCA-to-1/4" cables come, its main out carries the record player
  into the 18i8's inputs 5-6.
- **To be replaced in slots 2 and 3** (the player, 2026-10-10). The
  leading option is a Scarlett 2i2 (3rd or 4th Gen) for the mics and the
  Pi's playback. Its output feeds BMG-04F channel 1 (and 2) as a line
  input. The BMG-04F stays as the room's mixer: records on channel 3/4,
  main out to the speakers. Details: `replacing-the-mixer.md`.
- **Its inputs (2026-10-10):** the two SDCs on channels 1 and 2 (XLR). A
  record player on channels 3/4 (the stereo input), whose source button
  reads "MP3/USB" and "3/4". See "Shared mixer" under Interactions.
- **The panel** (2026-10-10):
  - **Inputs:**
    - ch1 and ch2: XLR/combo inputs, each with its own +48V button and LED
      (lit: phantom on) (the player, 2026-10-12);
    - ch3/4: RCA L/R (the record player) and 1/4" L/R.
  - **Channel strips:**
    - ch1 and ch2: GAIN, HIGH, LOW, EFF (send to the built-in DSP), PEAK
      LED, LEVEL;
    - ch3/4: GAIN, HIGH, LOW, the 3/4 ↔ MP3/USB button, PEAK LED, LEVEL.
  - **No monitor or AUX send:** every output carries the main mix.
  - **Outputs:** main L/R on XLR and on 1/4" (the 1/4" pair goes to the
    speakers), PHONES with its own level.
  - **Master:**
    - a display (it read "09 PC": PC is the USB-audio mode);
    - buttons REC, next/+, play/pause, previous/-, MODE (PC, BT, MP3,
      REC: it can record to a flash drive itself), DSP+ and DSP-;
    - PROGRAM (DSP);
    - PK! and SIG LEDs per side;
    - MAIN OUT and PHONES knobs.
  - **The USB-A socket** is labeled "MP3". It carries both the computer link
    and flash drives.
  - **Printed by it: "USB AUDIO → CH3/4, ← STEREO (PRE)".**
    - "→ CH3/4" matches T6.
    - "(PRE)" suggests the capture is taken before the main level. The
      laptop session found the capture silent with the main at zero.
      Re-test if it matters: record 10 s while turning the main from the
      middle to zero.
  - The EFF knobs were at minimum and the EQ at the middle on
    2026-10-10.
- **Interface:** a USB Audio Class device that Linux ALSA sees with no
  driver, with a fixed card id, a capture format, and any level controls.
- **Current: BOMGE BMG-04F.**
  - **Connection:** the mixer's USB-A socket, through an A-to-A link (a
    USB A-to-micro cable plus a micro-to-A adapter), into the computer's
    USB-A port.
    - A first try from the same socket to a laptop USB-C port, with
      another cable, showed nothing: that cable or port was the problem.
    - The dobro-soundfont notes say "micro-USB socket". That was a
      misreading in that session; the player corrected it on 2026-10-10.
  - USB id `e5b7:0811` (a JieLi BR21 chip). ALSA card id `spker`. USB
    full speed (USB 1.1).
  - **Capture:** S16_LE, 48000 Hz, 2 channels, the only format offered.
    **The two channels are identical sample for sample:** it is the mono
    main mix. There are no pan controls.
  - **It records after the main level.** With the main at zero, the
    capture is silent. The test used the main at halfway.
  - ALSA controls: `Mic Capture Volume` 0-127 (0 to +23.8 dB; left at
    127), `Mic Capture Switch`.
  - Its own floor, main at zero: -83 dBFS rms. The files carry about
    -3.8 LSB of DC offset. Hum at 55 and 111 Hz.
  - DSP effects: keep them off. Its EQ setting was not noted in the test.
  - **On the Pi** (2026-10-10, T1-T3 pass):
    - ALSA card 2, id `spker`. Use the id: the number depends on what else
      is plugged in.
    - Same capture and playback formats as on the laptop.
    - `Mic Capture Volume` starts at **0** on the Pi (127 on the laptop).
      The profile must set it: the laptop measurements are at 127.
    - `PCM Playback Volume` started at 192. The kernel reports it has no
      dB scale ("bogus dB values"), so it is a plain 0-255 setting.
    - Also on its USB: a **HID input device** (likely the mixer's
      play/pause/next buttons; they might serve as record/stop buttons
      later), and five USB mass-storage interfaces that the kernel ignores.
    - `vcgencmd get_throttled` stayed `0x0` with the mixer attached: it
      draws no meaningful power from the Pi.
    - A 5 s capture at `Mic Capture Volume` 127: no dropped frames;
      channels identical; DC -4.3 LSB; -79.5 dBFS rms, peak -60.5 dBFS
      (phantom was off).
    - **T5, 2026-10-10:**
      - Settings: both SDCs live, phantom on, every knob at the middle
        (gain, level, EQ), effects at zero, `Mic Capture Volume` 127.
      - A 40 s take of playing: peak -13.8 dBFS, no clipping. Per-second
        peaks mostly -14 to -25 dBFS, rms -31 to -42 dBFS.
      - Room tone with the mics live: about -72 dBFS (-71 in a pause in
        the take).
      - So hard notes stand about 58 dB above the room, playing 30-40 dB.
        About 6 dB of channel gain is left before the hardest peaks near
        -6 dBFS.
      - With the mic channels at zero, the floor is -79 dBFS (T4 in part).
      - **First take played back over the speakers** (the T5 take,
        2026-10-10): boosted +12.8 dB in software (peak -1 dBFS),
        `PCM Playback Volume` 255, channel 3/4 level and main at the
        middle, speakers at mid volume. The player: "nice". More level is
        available from channel 3/4's level knob and the speakers.
    - **Noise on playback, 2026-10-10.** The player heard "quite a bit of
      audible noise" in takes played back. A soft 16 s take (peak
      -25.1 dBFS, gated rms -43.5) had been boosted +23.3 dB. The floors,
      same knobs as T5, as dBFS rms (A-weighted in brackets):
      - mic channels at zero, or phantom off: -79.5 (-84). Mostly hiss,
        1-10 kHz. It comes after the channel levels (the main bus or the
        converter), so turning the mics down does not lower it;
      - mics live, nobody playing: -72.4 (-80.3). The mics add room noise,
        mostly below 1 kHz, with lines at 52-56, 80, 105 and 120 Hz;
      - so about half of the audible noise is the mixer's own hiss and
        half is the room.
      - The boost raised that floor to -49 dBFS in the played file, 7 dB
        more than for a take of normal playing (+16 dB). The boost is now
        capped at 16 dB (`max_gain_db`), so soft takes play softer.
  - Power: the player is fairly sure it has its own supply and draws
    none from USB (2026-10-10). Confirm in step 2: `vcgencmd
    get_throttled` on the Pi stays `0x0` with the mixer attached.
  - The sellers' listings also claim Bluetooth input and MP3 playback from a
    flash drive. They disagree on the outputs. Check the panel itself.
  - Bluetooth input works (the player, 2026-10-11). It has to be switched
    on and off with MODE, and it drops when the phone goes out of range.
    So the player does not use it for music in the room.
- **Profile values** (what the capture profile will hold): USB id, card id
  `spker`, format S16_LE, rate 48000, channels 2, channel layout
  "mono, duplicated" (keep channel 0), DC removal on, capture gain control
  `Mic Capture Volume` 0-127, set to 127.
- **Swap:**
  1. Plug in and probe: `lsusb`, `arecord -l`,
     `/proc/asound/cardN/stream0`, `amixer -c ID contents`.
  2. Write its profile. Set `capture` in the config.
  3. If its format differs (24-bit, true stereo, another rate), the stored
     take format follows the profile. Check that playback still handles it
     (T6).
  4. Run T1-T5, T7-T9, T11.
  5. If it also has outputs, decide whether it takes over slot 3 too.

### 3. Playback output

- **Role:** turn takes back into analog for the speakers.
- **Current (2026-10-14): the 18i8's USB playback,** S32_LE, 8 channels at 44.1/48 kHz. The
  recorder sends mono (or a take mixed to mono) to every channel; PCM 1-2 reach the speakers.
  Its internal mixer, set 2026-10-14 and pinned in the profile: Mix A = input 5 + PCM 1, Mix
  B = input 6 + PCM 2, at unity (160 on 0-184, -80 to +12 dB); outputs 1-2 (monitor) = Mix
  A, Mix B. The mic inputs are in no mix, so they never reach the speakers (requirement 2 in
  `replacing-the-mixer.md`). Before: outputs 1-2 = PCM 1-2 (factory). Speaker volume is the
  front monitor knob (the outputs' volume control is "HW"). The ambient synth plays here too
  (`plughw:CARD=USB,DEV=0`). The BMG-04F's playback, described below, is out of use.
- **Interface:** an ALSA playback device with a fixed card id and format,
  plus a volume control the software can set.
- **Current: the BMG-04F's USB playback stream.** It enters on **channel
  3/4**, the stereo channel the record player also uses. The **"3/4 / USB"
  button** on that channel picks one: the record player (3/4) or the
  computer (USB). It goes from there into the main mix, then out of the
  output the speakers use (found 2026-10-10).
  - With the button on 3/4, USB playback is silent: no sound and nothing
    in the capture (-106 to -110 dBFS at 1 kHz).
  - Channel 3/4's level knob scales playback only, not the mics.
  - Playback format: S16_LE, 48000 Hz, 2 channels.
  - ALSA controls: `PCM Playback Volume` 0-255 (was 102), `PCM Playback
    Switch`.
  - **T6 (2026-10-10): pass**, with the playback target under "Shared
    mixer" in Interactions. First pass: a 1 kHz tone at -20 dBFS, with
    every knob at the middle and the button on USB.
    - The player found it very quiet at `PCM Playback Volume` 100.
    - In the capture, with the mics live: -51.5 dBFS at 100, -47.9 at 160,
      -41.1 at 255. So 100 to 255 is +10.4 dB.
    - At 255 the player found it "quiet still, but not super quiet", with
      the speakers at mid volume. Takes will need more level: normalize on
      playback, and turn channel 3/4's level up (it does not raise the
      mics).
  - **T7 (loopback), 2026-10-10: yes.**
    - With both mic channels' levels at zero, the tone at
      `PCM Playback Volume` 255 came back in the capture at -31.4 dBFS,
      steady (-31.2 to -29.1 per 100 ms). Floor before and after:
      -79 dBFS rms.
    - So anything played during a recording is in the take: an old take,
      a click, a drone. The software knows exactly what it played, so it
      could subtract it later, if a feature needs that.
    - The same playback setting earlier gave -41.1 dBFS with the mics
      live. The later measurements (below) agree with -31.4, so a knob
      probably moved in between.
- **Profile values:** card id `spker`, format S16_LE, rate 48000,
  channels 2, volume control `PCM Playback Volume` 0-255.
- **Swap:**
  1. Probe as for slot 2. Write its profile. Set `playback` in the config.
  2. If its rate differs from the takes' rate, playback converts (ALSA
     `plughw` or a resample in the service).
  3. Run T1-T3, T6-T9, T11.

### 4. Speakers

- **Role:** play takes into the room.
- **Interface:** an analog line input, with its own power and volume.
- **Current: a pair of JBL 3 Series MkII studio monitors** (powered; the
  size, 305P/306P/308P, is not noted). They are across the room from the
  piano and the mics, with the piano between. Connected to the BMG-04F.
  - Each speaker plays one channel and has **one XLR and one 1/4" TRS
    input**, both balanced, plus a +4 dBu / -10 dBV sensitivity switch and
    a volume knob (JBL's spec pages).
  - **Only one source per speaker.** JBL's 3 Series MkII owner's manual:
    "Connect only a single signal source to the speaker using either the
    XLR INPUT connector, OR the 6mm (¼") TRS INPUT connector. Do not
    connect signal sources to both input connectors simultaneously."
    - A forum report on the earlier LSR305 says the two inputs are wired in
      parallel, so two sources would drive each other's outputs.
    - So the free XLR input cannot take a second source. Combine sources
      before the speakers, in a mixer.
  - **Wiring (2026-10-10):** the mixer's 1/4" outputs go to the speakers'
    1/4" TRS inputs. The speakers' **XLR inputs are free**.
  - Volume: mid for the first tests, then turned up for records (the one
    setting under "Shared mixer").
- **Swap:** no software change. Run T6 and T8.

### 5. Control unit

- **Role:** run the recorder service. It holds the audio devices, stores
  takes and serves the phone page.
  - Since 2026-10-11 it also runs the ambient synth (port 8001) and the
    home page (port 80). See "A second program on the control unit".
- **Interface:**
  - Linux with ALSA, Python 3, systemd;
  - USB-A ports for the audio devices;
  - a network link at a fixed address;
  - fanless, so it is silent in the room.
- **Current (2026-10-14): ECS LIVA** (DMI "ECS BAT-MINI", BIOS 5.6.5 of
  2014-07-28), in place of the Pi 3 after the Pi's T6 popped with the 18i8
  (swap log). Celeron N2807 (Bay Trail-M, 2 cores, 1.58 GHz), 1.8 GiB, a
  29 GB eMMC, fanless.
  - **Buses:** 2 USB-A ports on the SoC's Intel xHCI controller. Ethernet
    (Realtek RTL8111G) is on PCIe and the Wi-Fi/Bluetooth chip (Broadcom
    BCM43241, 2.4 and 5 GHz) on SDIO, so the 18i8 has the USB controller
    to itself. It enumerates at high speed on `xhci_hcd` with the scarlett2
    driver. Its ALSA card id stays `USB`, but its index changes between
    boots (0 or 1); the profiles use the id.
  - **OS, 2026-10-14:** upgraded in place over SSH, not installed fresh.
    It had Debian 10 from an earlier use, with old services (Quassel,
    PostgreSQL, Samba, Transmission, exim, lighttpd) and old projects.
    Those were archived on the desktop and removed, then the system was
    upgraded through Debian 11 and 12 to Debian 13.7 (trixie), kernel
    6.12.111+deb13-amd64.
    - Hostname `practice`. The user (as on the Pi) logs in with the
      desktop's key and has sudo without a password. Root also logs in
      with the key. Password login is off.
    - Time zone and locale as on the Pi (`local/README.md`).
    - Packages: the setup scripts' own, plus `firmware-brcm80211`,
      `firmware-realtek`, `intel-microcode`, `network-manager`,
      `avahi-daemon`, `iw`.
    - GRUB also installs to the removable path (`EFI/BOOT/BOOTX64.EFI`),
      so it boots even if the firmware loses its boot entry. The menu
      waits 5 s: hold Shift or press Esc at power-on for older kernels.
    - The first reboot into the new kernel (5.10, during the upgrade)
      hung until a power-cycle; the journal shows a clean shutdown and
      nothing after it. Since then, 3 reboots on 6.12 came back by
      themselves in 46-50 s.
  - **Deployed 2026-10-14:** practice-recorder and ambient-room
    (`tools/deploy`). The Pi's four `/etc/*.toml` were copied over as they
    were, since nothing in them names the Pi: `address` is unused, and
    the ambient synth names the card `plughw:CARD=USB`. The Pi's takes,
    exercise takes, exercises, backings and ambient state were copied too.
    All four services answer and see the 18i8. Nothing has opened the
    18i8 yet.
  - **Configs on deploy:** `setup/install.sh` writes each `/etc/*.toml`
    only when it is missing, so a change to `config/*.toml` reaches the
    unit only by hand: diff the installed copy against the repo's, install
    it, restart that service (the exercise app's `keep_channels`,
    2026-10-15).
  - **Wi-Fi:** slot 7 (the board's nvram, a pinned MAC address, 5 GHz).
  - **The USB audio driver:** `options snd_usb_audio lowlatency=0` in
    `/etc/modprobe.d/snd-usb-audio.conf` (2026-10-15). It gave fewer
    playback dropouts with the 18i8 in T9 (swap log). Delete the file to go
    back to the default.
  - **T9's sample-exact check** (a sine played and captured back
    digitally, its phase checked every 100 ms) is in `~/t9-ll0/` on the
    LIVA: `t9-loopback.py MINUTES OUTDIR`, then `t9-events.py OUTDIR`.
    Write OUTDIR to the eMMC, not `/tmp`, which is a 930 MB tmpfs. It
    found what `hwcheck` cannot: zero samples inserted into the playback,
    with no xrun reported.
  - **Done on the LIVA:** T6, T9 and T10 (swap log). Not measured yet:
    T1-T4, T11, the line-up's time after a take (23-27 s on the Pi 3),
    and the ambient synth's CPU playing live.
  - **The ambient synth's CPU, offline, 2026-10-15:** after ambient-room
    b8cd473 was deployed (the dobro held by string, the player's own bass,
    GeneralUser GS's guitar and banjo), its Big Sciota and Red Wing moods
    rendered offline for 20 s took about 8% of one core each, with all
    eight sampled voices loaded. Its Csound is x86_64, so the Pi's two
    SoundFont bugs do not apply; the workarounds stay, harmless.
- **Before, 2026-10-10 to 2026-10-14: Raspberry Pi 3 Model B, Rev 1.2:** 4 cores at 1.2 GHz, 1 GB. 4 USB-A ports,
  2.4 GHz Wi-Fi, 100 Mb Ethernet. No real-time clock: the time comes from
  NTP.
  - **OS, written 2026-10-10:** Raspberry Pi OS Lite (64-bit), the
    2026-10-06 release (Debian 13, Trixie). It is on a 32 GB Transcend SD
    card, set up headless by cloud-init from `setup/rpi/`
    (`setup/rpi/README.md`):
    - hostname `practice`, one user, SSH key only, sudo without a
      password;
    - the local time zone, en_US.UTF-8 (the user name and time zone are
      in `local/README.md`, not in git);
    - updated 2026-10-10 (kernel 6.18.50+rpt-rpi-v8); `alsa-utils`, `flac`
      and `python3-venv` installed.
  - The card had Raspbian 9 (under another hostname), last used about
    2025-01. The player needed nothing from it.
  - **Power:** a 1.8 A supply (below the 2.5 A advice). `vcgencmd
    get_throttled` was `0x0` after boot and after a reboot, with nothing
    on USB.
  - SSH from this desktop: `ssh practice` (a `Host practice` entry in
    `~/.ssh/config`, with the Pi's reserved address and the user).
  - **No USB Wi-Fi adapter.** It used to have an Edimax nano adapter
    (2.4 GHz only, like the built-in Wi-Fi). It was removed so the USB bus
    is free for the audio devices: the built-in Wi-Fi is not on USB.
  - Its CPU is enough: one capture and one playback stream at 48 kHz,
    16-bit, stereo are about 0.4 MB/s.
  - **The ambient synth's CPU, 2026-10-11:** each of its 25 moods
    rendered offline for 20 s (`tools/calibrate 20 moods` in ambient-room)
    took 19-39% of one core; the heaviest were Jazz club and Music box.
    Playing live, the whole service (Csound, the conductor, the web
    server) took 44% of one core for Jazz club and 38% for Bluegrass, with
    no dropouts; the load average was 0.43 (of 4 cores).
    After the acoustic voices (a bowed-string waveguide fiddle, mandolin,
    banjo ...), the same renders, 2026-10-11: 18-38% for the 27 moods
    (Bluegrass 25%, Country 33%); Bluegrass at its densest melody (Notes 1,
    about 22 notes a bar) 24% with the fiddle, 38% with the mandolin.
    With sampled voices (SoundFonts), 2026-10-11: Bluegrass 17%, Folk
    song 14%, Country 28%. Recordings cost less than synthesis here.
  - **Its Csound** (Debian 1:6.18.1+dfsg-4, aarch64) has two SoundFont
    bugs that x86_64 does not show: NaN from the stereo players, and
    tuning fields read as unsigned chars. ambient-room works around both
    (`sounds/README.md` there). A different control unit, or a newer
    Csound, may not need the workarounds; they are harmless either way.
  - The risk is its USB controller, which has a history of glitches with
    full-speed USB audio. T9 tests that. A weak power supply causes USB
    glitches too: use 5 V, 2.5 A, and `vcgencmd get_throttled` should say
    `0x0`.
  - **T9, 2026-10-10: fail** (with the BMG-04F, arecord's default buffer
    of about 0.5 s).
    - Over 30 min, 4 overruns (283, 173, 467, 140 ms): 3.1 s of audio
      lost against the wall clock.
    - The kernel logged "Transfer to device 4 endpoint 0x3 ... failed -
      FIQ reported NYET. Data may have been lost": the Pi 3's USB
      controller (dwc_otg, its FIQ split-transaction handling for
      full-speed devices) dropping capture data.
    - Not under quiet conditions: heavy test jobs (pure-Python analysis,
      file writes, playback) ran on the Pi during the soak.
    - `throttled` stayed 0x0, so power was not the cause.
  - **T9, clean re-run, 2026-10-10 16:08-16:38: pass.**
    - Run by `tools/hwcheck --soak 30`, with the profile's 2 s buffer and
      100 ms periods, and the service idle.
    - 1800.0 s of audio in 1800.1 s, 0 xruns, 0 kernel USB errors,
      throttled 0x0 throughout.
    - So the first failure came from the test load plus arecord's
      default 0.5 s buffer.
    - The Pi 3 stays in slot 5. Re-run T9 with the next capture interface.
  - **Play-along takes, 2026-10-12: data lost in duplex.** Recording while
    playing a backing (PLAN.md step 5), one take of two lost 29 pieces of
    its playback stream ("Transfer to device 4 endpoint 0x3 failed - FIQ
    reported NYET"), heard as crackles; it began while the service was
    still analyzing the take before. throttled 0x0. Capture alone, minutes
    later: no errors.
  - Switched off 2026-10-14, its card unchanged (Raspberry Pi OS, the
    services and a copy of the data as they were).
- **Alternates:**
  - the Pi 3 above;
  - a MacBook Air A1466 (the player's, 2026-10-14). It needs Linux, and
    `HandleLidSwitch=ignore` and `HandleLidSwitchExternalPower=ignore` in
    `/etc/systemd/logind.conf` to run with the lid closed. Its Wi-Fi is a
    Broadcom BCM4360 on 2013 and later models, which needs the `broadcom-sta`
    driver (built by DKMS on every kernel update). It has a fan.
- The hostname `practice` names the role, not the box: a replacement
  control unit takes the same name. `~/.ssh/config` has `Host practice`
  (the user) and `Host liva` (root) for it (`local/README.md`).
- **Swap:**
  1. Install a fresh OS, with the hostname `practice` (for a Pi:
     `setup/rpi/`), or bring an old one up to date (the LIVA, 2026-10-14).
     Run the setup script (packages, user, systemd unit).
     Deploy. Copy the config, with the new host's details.
  2. No code change: the service is plain Python, so arm64 or amd64 does
     not matter.
  3. Move the address reservation to the new box (slot 7).
  4. Run T1-T4, T6, T9-T11.

### 6. Storage

- **Role:** hold the takes.
- **Interface:** a directory with room, written at about 0.1 MB/s while
  recording.
- **Current (2026-10-14):** the LIVA's eMMC. After setup and the copy of
  the Pi's data, 20 GB free. 16-bit, 48 kHz mono is about 350 MB an hour,
  so that holds about 57 hours.
- **Before:** the Pi's SD card, a 32 GB Transcend. After setup, 2.8 GB
  used and 25 GB free (2026-10-10).
- **Later:** copy takes to the desktop or Dropbox (rsync or rclone). That
  is a separate part, not a change to this slot.
- **Swap** (a USB drive, a bigger card): set `takes_dir`, move the takes.
  Run T9.

### 7. Network

- **Role:** connect the phone (and this desktop) to the control unit.
- **Interface:** the control unit at a fixed IPv4 address on the home LAN.
- **Current:**
  - The addresses, network names and MAC addresses of this install are
    in `local/README.md` (not in git).
  - One home LAN (IPv4, a /24) behind the router, with addresses from
    DHCP.
  - This desktop, on Ethernet.
  - **The home Wi-Fi** is on 2.4 GHz (channel 10, seen from the Pi) and
    5 GHz, in mixed WPA2/WPA3 mode. The Pi 3B's Wi-Fi is 2.4 GHz only and
    joins it with WPA2 (key management `wpa-psk`).
  - The router also offers an IoT network (2.4 GHz, WPA2 only) for older
    gadgets. It is not needed. It may be isolated from the main network,
    which would cut the phone off from the control unit.
  - **The control unit, since 2026-10-14 the LIVA:** built-in Wi-Fi
    (`wlan0`) to the home Wi-Fi, at its reserved address (DHCP, reserved by
    the player 2026-10-14; a new address, so the phone's bookmark changed).
    NetworkManager connections:
    - The Wi-Fi connection is named after the network, copied from the
      Pi's: WPA2 (`wpa-psk`), autoconnect on, power saving off.
    - It pins the Wi-Fi MAC address (`cloned-mac-address`), because the
      board has none of its own. Its nvram holds Broadcom's placeholder
      (`00:90:4c:c5:12:38`), so the driver makes up a random address at
      every boot. The pinned one is the first random one, a locally
      administered address (`local/README.md`).
    - Ethernet: `wired`, DHCP. It isn't plugged in in the music room.
    - `/etc/resolv.conf` is written by NetworkManager. dhcpcd, dhclient,
      rdnssd and resolvconf were removed.
  - **The LIVA's nvram and 5 GHz.** The Wi-Fi chip's nvram is in an EFI
    variable, which the driver (`brcmfmac`) reads when no file is given.
    It says country `XT` (regrev 26). With it, the chip found no 5 GHz
    networks at all, not even the neighbours', though `iw list` showed the
    channels enabled.
    - A copy with `ccode=US` and `regrev=0` at
      `/lib/firmware/brcm/brcmfmac43241b4-sdio.ECS-BAT-MINI.txt` (no package
      owns that name; the driver tries it first) makes 5 GHz work, and the
      LIVA joins the router's 5 GHz by itself (same network name).
    - To make it again:
      `cat /sys/firmware/efi/efivars/nvram-74b00bd9-805a-4d61-b51f-43268123d113 | tail -c +5 | tr '\000' '\n' | grep -v '^$' | sed -e 's/^ccode=.*/ccode=US/' -e 's/^regrev=.*/regrev=0/'`
      into that file, then `modprobe -r brcmfmac; modprobe brcmfmac`.
      Do it over Ethernet, or with a fallback that removes the file if
      the router stops answering, because Wi-Fi may be the only link.
  - **In the music room, the LIVA** (2026-10-14, Wi-Fi only):
    - **2.4 GHz** (channel 2-3, -49 to -50 dBm, 85%):
      - 20 pings to the router: 0% loss, 9.6 ms average, 2.2-40 ms.
      - Throughput over SSH, 15 MB: LIVA to desktop 1.1-1.4 MB/s (once
        0.34 MB/s over HTTP), desktop to LIVA 2.2-3.0 MB/s.
      - The link rate was 24-144 Mbit/s, so the bottleneck is not the
        radio. Bluetooth off made no difference.
    - **5 GHz** (channel 100, -66 dBm, link rate 135-243 Mbit/s):
      - LIVA to desktop 2.6 MB/s, desktop to LIVA 3.1-3.9 MB/s.
      - 20 pings from the desktop: 0% loss, 6.6 ms average, 9 ms maximum.
      - Channel 100 is a DFS channel: if the router hears radar it moves,
        and the LIVA follows or falls back to 2.4 GHz.
    - It came back by itself on Wi-Fi alone after 2 reboots, at the
      reserved address.
    - A 5-minute mono take (about 29 MB) copies to the desktop in about
      11 s on 5 GHz.
  - **The Pi, before:** built-in Wi-Fi (`wlan0`) to the home Wi-Fi, at
    its reserved address (DHCP). It is a NetworkManager connection named
    after the network: autoconnect on, Wi-Fi power saving off. The Wi-Fi
    country is set (`setup/rpi/`). It came back by itself after a reboot.
    - Ethernet (`eth0`) has its own MAC address, so it gets another DHCP
      address when plugged in (2026-10-10).
    - The Pi's Wi-Fi address is reserved for its Wi-Fi MAC address on the
      router (the player, 2026-10-10).
    - **In the mixer's room** (2026-10-10, Wi-Fi only):
      - the home Wi-Fi on channel 3, -55 dBm (63%);
      - 20 pings to the router: 0% loss, 18 ms average, 1.4-78 ms.
  - `practice.local` resolves from the desktop (avahi runs on the control
    unit).
  - `.local` names work from the desktop, but not reliably on Android, so
    the phone uses the address.
- **Swap** (a new router, Wi-Fi to Ethernet): re-make the reservation.
  Update `~/.ssh/config` and the phone's bookmark. Run T10 and T11.

### 8. Client

- **Role:** the remote: start and stop recording, list takes, play them.
- **Interface:** a current browser on the same LAN. It talks only to the
  service's web page and HTTP API.
- **Current:** the player's Android phone (planned).
- **Swap:** no change. Run T11.

## Interactions

Some behaviour comes from two parts together. Re-check these whenever
either part is swapped.

- **Speakers while recording** (slots 2, 3, 4). The BMG-04F records after
  its main level. If the speakers hang off the main, the mics come out of
  them while recording, and stay live while a take plays back. In the
  laptop test this was soft: the player heard the mics only when tapping
  one.
  - **T8, 2026-10-10: pass.** Speakers at mid volume, every mixer knob at
    the middle: the player could not hear the speakers while playing. The
    speakers are across the room, with the piano between.
  - **T8 re-check, 2026-10-10: open.** The speakers were turned up to the
    record-listening level (the "one setting").
    - Standing near the speakers while someone else played, the player
      heard the piano through them clearly.
    - From the bench it is still to check. At mid volume it was not
      audible there.
    - Either way, anyone near the speakers hears the piano amplified while
      the mics are live.
  - On this mixer, the recording and the speakers both come from the main
    mix after the main level. So the live mics play in the speakers at
    whatever volume suits records. No knob setting or software can
    separate them.
  - Re-check after moving the speakers or mics, or after turning the main
    or speakers up. The options:
  - **A. Gain staging, all in software.** Speakers' volume down, playback
    louder: normalize each take on playback (a take peaking at -12 dBFS
    gains 12 dB) and raise `PCM Playback Volume`. No hardware change.
  - **B. Split slot 3 off the mixer.** Feed the speakers from the control
    unit through a USB DAC (or the Pi's headphone jack, which is noisy).
    Disconnect them from the mixer. That is a slot 3 swap: a new playback
    profile, then slot 3's tests.
  - **C. Switch the speakers' power** with a smart plug the control unit
    controls. They may pop. Last resort.
  - **D. Upgrade slot 2** to a USB interface that records before any output
    level (2 inputs, direct monitoring off). It is likely to fill slot 3
    too. That is the expected later upgrade anyway: 24-bit, quieter
    preamps, true stereo.

  Try A, then B. D comes with the upgrade.
  - **A does not work with the one-setting goal:** it needs the speakers
    down, and records use the same speakers.
  - **B, in more detail:**
    - the record player goes straight to the speakers, if it has its own
      phono preamp (line out);
    - the Pi's playback goes to the speakers' second input (the Pi's
      headphone jack as a free first try, or a USB DAC);
    - the mixer's main output is disconnected from the speakers, so the
      mixer only does the mics and the capture.

    This also ends the 3/4 ↔ MP3/USB button.
  - ~~B through the speakers' free XLR inputs~~: ruled out. The JBL manual
    allows one source per speaker (slot 4).
  - **The fix chosen instead:** a separate interface for the mics and the
    Pi, feeding a BMG-04F channel. See `replacing-the-mixer.md`.
  - The notes below on option B are kept for the record.
    - It needed each speaker to play its XLR and TRS inputs together: it
      can't.
    - The mics would still reach the speakers through the mixer. Fully
      removing them also needs the record player off the mixer, or a
      monitor send on the mixer that leaves the mics out (to check on the
      panel).
    - Worth it only if T8 from the bench shows the bleed bothers the
      player.
- **Loopback** (slots 2, 3). Playback comes back into the capture: T7
  found it on 2026-10-10, through channel 3/4 into the main mix. Playing
  along with a take, or a click, ends up in the new take.
  - Play-along takes use it (2026-10-12, PLAN.md step 5): the backing in
    the take lines the take up to the sample and is subtracted. The
    profile says so (`loopback = true`). A device without loopback needs
    another way to line takes up.
- **Shared clock** (slots 2, 3). Capture and playback on one device share
  a clock. Split across two devices (option B), they drift slightly apart.
  That matters only for features that line a new take up with an old one.
- **USB power** (slots 2, 5). If the mixer draws its power, phantom
  included, from the control unit's USB, a Pi 3's ports (1.2 A shared) may
  not be enough. Checked in step 2, then T9.
- **Phantom power** (slots 1, 2). A condenser needs the capture
  interface's phantom power on. It was off on 2026-10-10. Switch it with the
  mic channels' levels down, to avoid a thump in the speakers.
- **Shared mixer: the record player** (slots 2, 3, 4). The BMG-04F and the
  speakers are also the room's record player system (channels 3/4).
  - **One setting for both** (2026-10-10):
    - every mixer knob at the middle, effects at zero;
    - the speakers somewhat above mid ("good for records", the player);
    - only the 3/4 ↔ MP3/USB button changes between records and the
      recorder.
  - **Calibration:** records and takes pass through the same knobs, so the
    capture compares them directly. At this setting, a record played at
    -34.2 dBFS gated rms in the capture. The T5 take, peak-normalized to
    -1 dBFS (file gated rms -24.2), played at -36.3: 2.1 dB under the
    record. USB playback to capture: -12.1 dB. The player: "very
    reasonable", a little louder would be nice.
  - **So the playback target** is a take's gated rms of about -21 dBFS,
    with a peak limiter at -1 dBFS. That puts takes level with records or
    a little above. Piano takes have a crest factor of about 23 dB, so the
    limiter trims only the hardest attacks, by about 3 dB. The boost is
    capped at +16 dB, because it raises the noise too (slot 2, "Noise on
    playback").
  - Re-calibrate (a minute of a record, and a take, both captured)
    whenever slot 2, 3 or 4 changes, or the knob setting does.
  - The record's loudest instant reached -0.5 dBFS in the capture (a pop,
    likely). That only matters when recording while a record plays.
  - The recording is the main mix. A record playing during practice ends up
    in the take, which could also be a feature: playing along.
  - The main level sets both the recording level and the listening volume
    for records.
  - The recorder must not leave things changed for record listening. For
    example, it should not leave `PCM Playback Volume` blasting, or the
    speakers switched off.
  - **The record player and the recorder's playback share channel 3/4.**
    Its "3/4 / USB" button picks one, by hand. With it on 3/4, the
    recorder can't play takes. With it on USB, records are silent. The
    phone page should show this, or at least remind.
  - If slot 2 or 3 is swapped (an interface, a USB DAC), the record
    player's path to the speakers has to keep working. Taking playback off
    channel 3/4 would also free the button.
- **A second program on the control unit** (slots 3, 5). Since
  2026-10-11: an ambient synth, `~/play/ambient-room`, on port 8001. It
  plays through the same USB playback device.
  - **They take turns; they never play at once.** The player asked for
    this, so the system never ends up "with both things going at once".
    So no `dmix`: the playback profile keeps `hw:CARD=spker`.
  - **The hand-over** (`recorder/peers.py`): before recording or playing,
    each program asks the others in its config's `peers` to let go
    (`POST /api/yield`).
    - The synth fades out in 1.5 s and closes the device. A recording
      starts about 3 s after the button.
    - The recorder stops a take that is playing. It refuses while
      recording, so the synth will not start then.
    - Tested on the Pi, 2026-10-11: a recording faded the synth out; the
      synth's Play was refused during the recording.
  - **Load:** the synth uses 19-30% of one core while it plays (measured
    by rendering each mood offline). It never runs during a recording, so
    T9 does not change.
  - **The home page** (`home/server.py`, port 80): http://practice.local/
    (the phone uses the reserved address) shows what each program is
    doing and links to each. "Stop everything" stops a recording, a take
    and the synth.
  - **A third program** (2026-10-13): the exercise app (`exercises/`,
    port 8002; PLAN.md step 6). It records like the practice recorder and
    takes turns the same way: it asks 8000 and 8001 to let go, and they
    list it among their peers.
  - **The exercise app listens between takes** (2026-10-16): while its page
    is on a choosing step or the record screen, it opens the capture to
    show each input's level, until 5 s after the page last asked
    (`/api/monitor`; the page stops asking when hidden, or after 10 minutes
    untouched). The capture device is the practice recorder's too, so a
    yield (`POST /api/yield`) closes it and keeps it closed for 10 s: a
    peer that asks to record gets the inputs. If they are busy anyway, the
    page says so and tries again 10 s later.
  - **A fourth program** (2026-10-16): the chord app (`chords/`, port 8003; PLAN.md step 7). It only
    listens, and only while its page is open, the same way: it lets the inputs go for 10 s when
    asked. The two recorders list it among their peers, so a recording always gets the inputs; it
    asks no one itself, so if they are in use (a recording, or the exercise app listening) its page
    says so.

## Tests

Each test has an id, so a swap can say which to run. "Auto" tests will be
run by `tools/hwcheck` (to be written in step 3). "Player" tests need
someone in the room.

| id | name | how | passes when | who |
|---|---|---|---|---|
| T1 | present | `lsusb`, `arecord -l` / `aplay -l` | the device is there with the profile's USB id and card id | auto |
| T2 | format | `/proc/asound/cardN/stream0` | the profile's format, rate and channels are offered | auto |
| T3 | controls | `amixer -c ID contents` | the profile's controls exist, with the profile's ranges | auto |
| T4 | floor | 10 s capture, inputs quiet (main at the recording level) | noise floor, DC offset, hum and channel identity are recorded in the swap log; no worse than the last part, or the change is understood | auto |
| T5 | level | the player plays hard, then plays normally, 30 s | peaks between -20 and -6 dBFS with no clipping; room tone noted | player |
| T6 | playback | a test tone, then a take, at two volume settings | heard in the room; the volume control changes it | player |
| T7 | loopback | capture while playing a tone | the result is recorded (present or not, and its level) | auto |
| T8 | bleed | the mics live at the recording level, speakers at playing-back level | no ringing; the live sound is quiet enough for the player | player |
| T9 | soak | 30 min capture to `takes_dir`, with playback running part of the time | no xruns, no gaps; `vcgencmd get_throttled` is `0x0` on a Pi | auto |
| T10 | network | reboot the control unit; reach it from the desktop | SSH and the service answer at the reserved address | auto |
| T11 | end to end | from the phone: record, stop, list, play on the speakers | all of it works with no other device and no walk to the speakers | player |

## Swapping a part

1. Read the slot's entry above. Note what the new part does differently.
2. Probe it (T1-T3 by hand until `tools/hwcheck` exists). Write its
   profile if it is an audio device.
3. Change the config. Deploy.
4. Run the tests the slot lists.
5. Update the slot's entry and the summary table. Add a line to the swap
   log.

## Swap log

| date | slot | from | to | tests | result |
|---|---|---|---|---|---|
| 2026-10-10 | (start) | | the parts above | capture checked on the laptop (dobro-soundfont) | see slot 2 |
| 2026-10-10 | 5, 7 | Raspbian 9, Edimax USB Wi-Fi | Raspberry Pi OS Lite 64-bit (2026-10-06), built-in Wi-Fi, on the home network | T10: moved to the mixer's room, booted on Wi-Fi alone, at the reserved address | pass |
| 2026-10-10 | 2, 3 | the laptop | the Pi (`practice`) | T1-T3, T5, T6 (quiet, needs more level), T7 (loopback: yes) | pass |
| 2026-10-10 | 5 | | the Pi 3 with the BMG-04F | T9 (30 min, under test load) | **fail**: 4 overruns, 3.1 s lost, a dwc_otg FIQ error |
| 2026-10-10 | 2, 3, 5 | | BMG-04F on the Pi 3, 2 s buffer (hwcheck) | T1-T4, T9 (30 min, clean) | pass |
| 2026-10-11 | 3, 5 | | + the ambient synth and the home page; hand-over between them | hand-over both ways; T9 not affected (never together) | pass |
| 2026-10-14 | 2, 3, 4 | BMG-04F | Scarlett 18i8 3rd Gen (the BMG-04F's main out into its inputs 5-6 for the record player, until RCA cables) | T1-T3 by hand; records heard through it (player); the recorder, exercise app and ambient synth switched (`/etc` configs edited, `*.bmg-04f.bak` kept) | partial: T4-T9, T11 due |
| 2026-10-14 | 2, 3 | | scarlett-18i8-gen3 | T4 (hwcheck, 10 s, raw 20 channels): inputs not plugged in -110.5 to -111.6 dBFS rms, the Nadine (input 3, on the bass) -96.5, the piano condenser (input 4) -57.5 (the room, the Pi silent), the BMG-04F's main out (inputs 5-6) -94; no xruns. T7: 1 kHz at -20 dBFS back at -64.0 dBFS on the Nadine's channel, through the air (-96.5 before), and heard in the room (player); the electrical copy is on channels 11-12, not kept | pass; T5, T6 (a take, two volumes), T8, T9, T11 due |
| 2026-10-14 | 1, 2 | | scarlett-18i8-gen3 | T5, the bass (60 s and 75 s, all bass; `~/t5-2026-10-14*.wav` on the Pi): the Nadine at first peaked -22.6 dBFS played hard; with input 3's gain knob at half (straight up; the knobs are not marked, and gain is not in software on the 3rd Gen), -6.1 at the loudest, which was bowed (the player: bowing is louder than any pizz), pizz about -10 to -15, no clipping, no xruns. The piano condenser (input 4) heard the bass across the room at peaks -19 to -26 dBFS | pass for the Nadine; then backed off to about 11 o'clock for headroom (not measured since); T5 for the piano condenser, T6, T8, T9, T11 due |
| 2026-10-14 | 1, 2 | | scarlett-18i8-gen3 | T5, the piano (45 s, piano alone; `~/t5-2026-10-14-piano.wav`): the condenser (input 4) peaked -0.8 dBFS played hard (5 s peaks -0.8 to -8.0), no samples clipped; the Nadine heard the piano at peaks -25 to -35 | too hot: input 4 to come down about 6-8 dB (hard playing to about -8) |
| 2026-10-14 | 1, 2 | | scarlett-18i8-gen3 | T5, the piano again (30 s; `~/t5-2026-10-14-piano-b.wav`), input 4's knob at about 8 o'clock: peaks -6.6 dBFS played hard, -9 to -17 normally, no clipping, no xruns. Knobs now: input 3 (the Nadine) about 11 o'clock, input 4 (the piano condenser) about 8 o'clock | pass |
| 2026-10-14 | 3, 4, 5 | | scarlett-18i8-gen3 on the Pi 3 | T6: the piano T5 recording (2 channels, mixed to mono by the recorder's playback, loudness +6.3 dB) played 10 s at the set level, 10 s 10 dB down, 10 s while the monitor knob turned: heard, with some popping (player). No USB errors in dmesg, `get_throttled` 0x0; xruns not measured | **fail** (popping); the player: move the control unit to the LIVA (slot 5's alternate) |
| 2026-10-14 | 5, 6, 7 | the Pi 3 (Raspberry Pi OS, 2.4 GHz Wi-Fi) | the ECS LIVA (Debian 13, upgraded in place; 5 GHz Wi-Fi with a board nvram), hostname `practice`, the 18i8 on its USB; the Pi switched off | T10: in the music room on Wi-Fi alone, rebooted twice, back by itself in 46-50 s at its reserved address; 20 pings 0% loss. All four services answer and see the 18i8 (not opened yet) | pass; T1-T4, T6, T9, T11 due |
| 2026-10-14 | 3, 5 | | scarlett-18i8-gen3 on the LIVA | T6: a 440 Hz tone at -20 dBFS on PCM 1-2 (10 s, `aplay` without `-q`: 0 underruns), then a take through the exercise app at the set level and 10 dB down: the step heard (player). That take (recorded on the Pi) popped; Down Yonder dobro take 1 (recorded on a camera, never on the Pi) through the practice recorder for 40 s: clean (player). The kernel logged nothing | pass |
| 2026-10-14 | 2, 5 | | scarlett-18i8-gen3 on the Pi 3 (found on the LIVA) | The takes scanned for one-sample steps (lost samples; click-track ticks excluded by their width). The 22 takes before the 18i8 (BMG-04F): 0. All 11 recorded on the Pi through the 18i8 (2026-10-14, 19:26-21:44): 4-17 each, about one every 2-3 s, many on the capture's 100 ms period grid; heard as pops when played on the LIVA. So the Pi 3 lost capture data with the 18i8, and its T6 popping was most likely in the recording it played (its own T5 take) | the Pi's 18i8 takes have gaps; T9 on the LIVA should check for lost samples, not only xruns |
| 2026-10-14 | 2, 3, 5 | | scarlett-18i8-gen3 on the LIVA | T9 in duplex, sample-exact: 30 min capture of all 20 channels (2 s buffer, 100 ms periods) while playing a 997 Hz sine at -50 dBFS on PCM 1-2. The 18i8 captures PCM 1-2 digitally on channels 11-12, where the sine came back at -50.00 dBFS. Its phase against the capture's sample count was checked every 100 ms. 1800.2 s captured in 1800.0 s; 0 overruns, 0 underruns (arecord and aplay without `-q`); load 0.1-0.3; the kernel logged nothing. Two events (at 50 s and 701 s), each exactly 12 zero samples (two 125 µs USB packets) inserted into the playback, after which the sine ran 12 samples late; a 20 s trial run had one, 1 zero sample. No capture samples were lost (a loss would advance the sine). The driver: implicit feedback for playback, `lowlatency=Y` (snd-usb-audio's default since kernel 5.15) | capture: pass. Playback: about one 0.25 ms dropout every 15 min, which ALSA does not report; to try: `snd_usb_audio lowlatency=0` |
| 2026-10-15 | 2, 3, 5 | `lowlatency=Y` | `snd_usb_audio lowlatency=0` (driver reloaded, not persisted) | The same T9, 120 min (07:29-09:29). In the first 2.3 s, two insertions of zeros (6 samples each) while the streams settled. After that, one event in 2 h, at 68 min: 46 zero samples (about 1 ms) inserted into the playback. 7200.2 s captured in 7200.0 s; no capture samples lost; 0 overruns, 0 underruns; load 0.1-0.4; the kernel logged nothing | capture: pass. Playback: fewer dropouts than with `lowlatency=Y` (1 in 2 h against 2 in 30 min) but this one was longer (1 ms against 0.25 ms). Kept: `/etc/modprobe.d/snd-usb-audio.conf` (2026-10-15) |

## Sources

- `~/play/dobro-transcription/dobro-soundfont/recordings/2026-10-10-mixer/notes.md`
  (the mixer measurements);
- `~/play/dobro-transcription/dobro-soundfont/docs/recording-setup.md`.

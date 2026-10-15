# Replacing the mixer (slots 2 and 3)

The player decided on 2026-10-10 to replace the BMG-04F with a device the
control unit can control over USB. This file holds the requirements and the
candidates. The facts behind them are in `components.md`.

**Chosen (2026-10-14): the Scarlett 18i8 3rd Gen,** bought by the player, for the music room
on the control unit (`practice`). Not yet connected; the swap follows "Swapping a part" in
`components.md` (probe T1-T3, write `hardware/` profile, config, deploy, the slot's tests,
swap log).

## What it must do

Each line comes from something found with the BMG-04F on 2026-10-10.

1. **Record the two condensers, with phantom power, independent of any
   output level.** The BMG-04F records its main mix after the main level,
   and only in mono at 16-bit. 24-bit is wanted.
2. **Keep the live mics out of the speakers, under software control, while
   the Pi's playback and the record player reach them.** On the BMG-04F
   the speakers carry the main mix, so the mics play through them (T8),
   and no knob can separate them.
3. **Give the record player an input routed to the speakers in hardware,**
   so records play with the Pi off or busy. On the BMG-04F the record
   player and the Pi's playback share channel 3/4 through one button.
4. **Linux, no special driver, with its controls exposed to software**
   (ALSA): input gain, phantom power, routing and mix levels.
5. **Balanced outputs** for the JBL 3 Series MkII monitors (1/4" TRS or
   XLR inputs).

## Leading option: a Scarlett 2i2 (3rd or 4th Gen) for practice, into the BMG-04F

The player's idea (2026-10-10), revised the same day. The JBL monitors take
**one source per speaker** (their manual), so the two paths are combined in
the BMG-04F, not at the speakers:

- **Practice:** both SDCs → the 2i2 ↔ the Pi. The 2i2's output (1/4" TRS)
  → BMG-04F **channel 1** (and channel 2, for stereo takes), as a line
  input into the combo jack.
  - With the 2i2's Direct Monitor off, its output carries only the Pi's
    playback: no mics in the speakers.
- **Records:** record player → BMG-04F channel 3/4, as now, with the
  button left on **3/4** for good.
- **Speakers:** BMG-04F main out (1/4") → the speakers' TRS inputs, as
  now: one source per speaker.
- **Knobs, set once:**
  - BMG channel 1/2 LEVEL: take volume;
  - BMG channel 3/4 LEVEL: record volume;
  - MAIN OUT: both together.
- **On the BMG-04F:** phantom off, EFF at zero on channels 1/2, and gain
  low enough for a line-level signal. Its USB is no longer used.
- **What it fixes:**
  - the mics in the speakers (T8);
  - the shared channel 3/4 and its button;
  - mono 16-bit capture;
  - the BMG-04F on the Pi's USB: it is a full-speed device, the path that
    failed T9. The 2i2 is high-speed.
- **Conditions:**
  1. **Power:** the 2i2 is bus-powered, phantom included, from a Pi 3 on a
     1.8 A supply. Check `vcgencmd get_throttled` after connecting it. If
     it is short: a 2.5 A supply or a powered USB hub.
  2. **Hum:** the 2i2 (powered by the Pi) into the BMG-04F (its own
     supply). Use balanced cables if the BMG's 1/4" inputs are balanced.
     If it hums, a ground-loop isolator.
- **To buy:**
  - the 2i2 (3rd Gen used, or 4th Gen);
  - 1-2 short 1/4" TRS to 1/4" TRS cables (perhaps at hand).

  The existing XLR cables stay with the mics.
- **Given up against the 4i4:** recording the record player (playing along
  with a record and capturing both). Takes and records are balanced by
  hardware knobs, not a software mixer.
- **Ruled out:** feeding the 2i2 straight into the speakers' free XLR
  inputs. JBL: "Do not connect signal sources to both input connectors
  simultaneously." A forum report on the LSR305 says the inputs are wired
  in parallel.
- **Linux:** the same `scarlett2` driver. Input gain, phantom and Direct
  Monitor are ALSA controls. A 2026 kernel patch fixed the 2i2 4th Gen's
  Direct Monitor *gain* control after firmware 2417. Direct Monitor *off*
  should be unaffected, but check the Pi's kernel before relying on the
  gain.
- **3rd Gen 2i2 works too.** It is discontinued, so mostly used, and
  cheaper.
  - **Its Linux controls:** Direct Monitor (off, mono, stereo), 48 V,
    Air, Inst, and "phantom power persistence" (48 V on at power-up, so
    the condensers survive a power cut).
  - **Hardware only:** input gain (analog knobs) and output volume. Set
    once by hand.
  - **Against the 4th Gen:** no software gain or autogain, somewhat noisier
    converters, no clip safe. Plenty for practice takes.
  - Either generation: turn off "MSD" (Easy Start) mode once, from Linux,
    for full control.
  - Source: [alsa-scarlett-gui, small 3rd Gen
    models](https://github.com/geoffreybennett/alsa-scarlett-gui/blob/master/docs/iface-small.md).

## Learned from the first play-along takes (2026-10-12)

The bass with the Nadine pickup, played along with a dobro backing (PLAN.md
step 5), added three requirements:

6. **Inputs for instruments besides the two condensers.** The Nadine took a
   condenser's channel: unplug, swap, re-gain, and back again for the
   piano. The condensers should stay plugged in, with at least one more
   input (better two) for instruments: the Nadine, a bass DI, a dobro
   pickup. The Nadine needs phantom power (the player, 2026-10-12), so it
   takes a mic preamp, not an instrument input. Counting the record
   player's pair: at least 3 mic preamps with phantom (4 for a second
   instrument), plus 2 line inputs.
7. **Each input recorded as its own channel.** The BMG-04F records one mono
   mix: the backing (electrically, about -41 dBFS, louder than the bass),
   the mics and the bass all in one track, and the backing could not be
   subtracted well. With separate channels, the bass's track holds only
   the bass and the backing stays out of every take.
8. **Gain per input from software,** so a "bass" and a "piano" setting can
   be saved and called up from the phone instead of turning knobs. Also
   high-speed USB 2.0: the BMG-04F (full speed) lost playback data in
   duplex on the Pi 3 (slot 5, 2026-10-12).

What this does to the candidates below: the 4i4 4th Gen has two preamps
(inputs 1-2) and two line inputs (3-4, the record player), so a third
instrument would again need a condenser's channel. The boxes with more
preamps move up: the 18i8 (2nd and 3rd Gen, listed below as "bigger than
needed") and the 4th Gen 16i16 and 18i16. Checked 2026-10-12 (web search):

| model | mic preamps | phantom | gain | Linux | price (US, Oct 2026) |
|---|---|---|---|---|---|
| Scarlett 18i8 3rd Gen | 4 (inputs 1-4; 1-2 also instrument; 5-8 line) | in pairs, 1-2 and 3-4 | knobs | `scarlett2` (mainline, mature); standalone mode | Focusrite refurbished $360-416 |
| Scarlett 18i16 4th Gen | 4 by Focusrite's comparison table (one retailer's title says 2: *to verify*) | switchable; per channel not confirmed | software, auto-gain | the FCP driver (Linux 6.14+; the Pi runs 6.18), plus `fcp-support` and a firmware update | about $500 new, $350-435 used |
| Scarlett 16i16 4th Gen | 2 | | | | out: too few preamps |

- Phantom in pairs is no obstacle: the condensers and the Nadine all need it.
- With a channel per source, knobs are set once per source and stay, so the
  18i8's analog gain also ends the re-gaining; software gain (18i16) is a
  convenience on top, with a newer driver to trust on the Pi.
- The 6i6 2nd Gen ("the bargain" below) has two preamps: out for this.

## Alternative: Focusrite Scarlett 4i4 (4th Gen), everything in one box

- **Inputs and outputs:**
  - inputs 1-2: mic/line/instrument (XLR combo), with phantom power per
    channel;
  - inputs 3-4: line (rear 1/4");
  - outputs 1-4: line (1/4" TRS), plus headphones;
  - over USB: 6 in and 6 out, with a 10×6 mixer inside.
- **On Linux:**
  - It uses the kernel's `scarlett2` driver, mainline since Linux 6.8. The
    Pi runs 6.18.
  - The driver exposes routing, the mixer, software input gain, autogain,
    per-channel 48 V and Air as ALSA controls. `alsa-scarlett-gui` is a
    GUI over those same controls.
  - There is no software output volume: the front knob sets it.
  - "MSD" (Easy Start) mode must be off for full function. A firmware
    update turns it off.
- **How it meets the requirements:**
  - Mics on inputs 1-2 and the record player on inputs 3-4 are all
    recorded at 24-bit, before any output level. The mics can be stereo
    now.
  - Line outputs 1-2 go to the speakers. They carry a mixer bus of the
    Pi's playback plus inputs 3-4 (records). The mics are not routed
    there, so nothing bleeds. Software can add them later if ever wanted.
  - **Records with the Pi off:** Focusrite says 4th Gen units pass inputs
    to outputs in standalone mode, with no computer, using the routing set
    up before. *To verify:* that routing set from Linux persists in the
    unit.
  - The 3/4 ↔ MP3/USB button problem goes away. The front knob sets
    listening volume for everything, and the mixer's crosspoint gains
    balance records against takes, in software.
- **Power:** it has a second USB-C port for external power. Use it: the
  Pi 3's USB ports can't be relied on for power.
- **Price:** about $225-300 in the US (October 2026 listings; a $349.99
  reference price).
- **Cables:**
  - record player to inputs 3-4: 2× RCA to 1/4" TS, if the record player
    has its own preamp (line out);
  - speakers: the existing 1/4" cables, to line outputs 1-2.
- **The swap**, by "Swapping a part" in `components.md`:
  1. Probe: T1-T3.
  2. Write `hardware/scarlett-4i4-gen4.toml`: the card id, S32_LE,
     6 capture channels, keep channels 0-1, and the routing and mixer
     settings as controls with set values.
  3. Then T4-T9 and the calibration against a record.
  - The hardware layer already reads S32_LE and keeps any channels.
    Routing settings are ALSA controls like any other, so the profile can
    set them.

## Consolidating: one box for mics, records and playback

Instead of the BMG-04F plus a 2i2, one interface could do it all. It needs:
- two mic preamps;
- a stereo line input for the record player;
- an internal mixer whose routing the Pi sets (records and the Pi's
  playback to the speakers, the mics only to the capture);
- ideally standalone mode, so records play with the Pi off.

Two lists decide the candidates:
- **Linux driver support,** with mixer and routing as ALSA controls (the
  alsa-scarlett-gui README):
  - Scarlett 1st Gen 6i6/8i6/18i6/18i8/18i20;
  - 2nd Gen 6i6/18i8/18i20;
  - 3rd Gen Solo/2i2/4i4/8i6/18i8/18i20;
  - 4th Gen Solo/2i2/4i4/16i16/18i16/18i20;
  - Clarett and Clarett+ 2Pre/4Pre/8Pre.
- **Standalone mode,** with routing saved in the unit (Focusrite):
  - 3rd Gen 8i6/18i8/18i20;
  - 2nd Gen 6i6/18i8/18i20;
  - 1st Gen 6i6/18i8/18i20;
  - all Clarett;
  - 4th Gen (the 4i4 guide).

  The 3rd Gen 4i4 is not on the list.

| model | record player in | records with the Pi off | notes |
|---|---|---|---|
| Scarlett 4i4 4th Gen | inputs 3-4 | yes | current; software gain; USB-C power port; about $225-300 new |
| Scarlett 6i6 2nd Gen (used) | inputs 3-4 | yes, own PSU | often the cheapest one-box option; Linux support since about 2019; check the PSU comes with it |
| Scarlett 8i6 3rd Gen (used) | inputs 3-6 | yes, own PSU | more inputs than needed; turn off MSD mode once |
| Scarlett 4i4 3rd Gen (used) | inputs 3-4 | only while the Pi is on | bus-powered, no standalone mode; may need a powered hub on a Pi 3 |
| Scarlett 18i8 2nd/3rd Gen (used) | spare line inputs | yes | bigger than needed |
| Behringer XR18 (used) | many | yes | full network control (OSC); class-compliant USB; big. The XR12/XR16 have no USB audio. |

- **Skipped:**
  - Behringer UMC404HD, MOTU M4 and similar: the monitor knob mixes in the
    mics.
  - Clarett 2Pre: no spare line inputs.
  - Behringer Flow 8: USB MIDI control is reported, but no command list was
    found.
- **Watch for:** a used 6i6 2nd Gen as the bargain, the 4i4 4th Gen as the
  clean new choice.
- **A consolidated box is a swap of slots 2 and 3,** as usual. The BMG-04F
  then leaves the system.
- **Standalone mode:** Focusrite says changes are saved in the unit
  shortly after they are made. *To verify:* that routing set from Linux is
  saved the same way.

## Considered

- **Scarlett 2i2 (4th Gen) alone,** with the record player wired straight
  into the speakers: it works too, but needs RCA-to-XLR cables, and loses
  the BMG-04F's volume knob and Bluetooth for records. The leading option
  above keeps the BMG-04F instead.
- **MOTU M4, Behringer UMC404HD and similar.** They are class compliant,
  but monitoring is a hardware knob that blends all inputs with playback.
  Records can't reach the speakers without the mics, except through the
  Pi, which then has to be on.
- **Digital mixers controlled over the network** (Behringer XR18 and
  similar). Fully controllable, but much bigger and dearer than needed.

## The control unit (slot 5)

T9 first failed on the Pi 3 with the BMG-04F, under heavy test load with
arecord's default 0.5 s buffer: a dwc_otg FIQ error, in the path for
full-speed devices. A clean re-run with a 2 s buffer passed: 30 min, no
loss.

The Scarlett is a high-speed USB device, which should avoid that path.
Run T9 with it anyway. If the Pi 3 fails, the LIVA (a standard xHCI USB
controller) takes slot 5.

## Sources

- Supported models: [alsa-scarlett-gui README](https://github.com/geoffreybennett/alsa-scarlett-gui).
- Standalone mode: [Focusrite: standalone mode](https://support.focusrite.com/hc/en-gb/articles/360006972619);
  [Focusrite: Using your Scarlett 4i4](https://userguides.focusrite.com/hc/en-gb/articles/18676395354258-Using-your-Scarlett-4i4);
  [Focusrite Control presets, 2nd Gen 6i6](https://support.focusrite.com/hc/en-gb/articles/115004431709).
- Flow 8 USB MIDI: [behringer.world forum](https://behringer.world/viewtopic.php?p=12239).

- JBL 3 Series MkII inputs: [owner's manual (jbl.com)](https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dw826522b1/pdfs/JBL_3_Series_MKII_Owners_Manual_English.pdf);
  [Gearspace: both monitor inputs at once](https://gearspace.com/board/audio-student-engineering-production-question-zone/1017946-can-i-use-both-studio-monitor-inputs-same-time.html).
- Linux support: [Phoronix: Scarlett 4i4 and others supported by Linux 6.8](https://www.phoronix.com/news/Focusrite-Scarlett-New-Linux-68);
  [alsa-scarlett-gui](https://github.com/geoffreybennett/alsa-scarlett-gui)
  and its [notes on the small 4th Gen models](https://github.com/geoffreybennett/alsa-scarlett-gui/blob/master/docs/iface-4th-gen-small.md).
- Standalone mode: [Focusrite: Using your Scarlett 4i4](https://userguides.focusrite.com/hc/en-gb/articles/18676395354258-Using-your-Scarlett-4i4).
- Prices: [Equipboard](https://equipboard.com/items/focusrite-scarlett-4i4-4th-gen-usb-audio-interface),
  [Alamo Music](https://alamomusic.com/products/focusrite-scarlett-4i4-usb-audio-interface-4th-generation).
- Input counts and prices, 2026-10-12: Markertek's 18i8 3rd Gen spec sheet
  (markertek.com/product/AMS-SCAR-18I8-3G), Focusrite's 18i16 page
  (us.focusrite.com/products/scarlett-18i16), gear4music's 16i16 4th Gen
  listing, the alsa-scarlett-gui README (github.com/geoffreybennett/alsa-scarlett-gui;
  the FCP driver for the large 4th Gen units), equipboard.com's 18i16 price
  tracker.


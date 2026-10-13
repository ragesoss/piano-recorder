# piano-recorder

A practice recorder: mics into a mixer, a small computer on the mixer's USB,
and a phone web page to record and play takes over the room's speakers.
The same control unit runs the exercise app (`exercises/`: exercise takes
for `~/play/dobro-transcription`, PLAN.md step 6) and the home page
(`home/`); the practice recorder itself is for the piano and its player.
Start with `PLAN.md` (the design and the steps), then
`docs/components.md` (the parts, their interfaces, tests and swap steps).

- **Modular by rule.** Every hardware part fills a slot in
  `docs/components.md`.
  - Hardware facts (ids, formats, controls, measurements) go in that
    slot's entry, with the date.
  - Code reaches hardware only through `hardware/*.toml` profiles and the
    config; never hard-code a card id, control name, format or address
    elsewhere.
  - A swap follows "Swapping a part" there and ends with a line in the swap
    log.
- **What's at hand first.** Build the whole system with the current gear
  before upgrading anything. Upgrade one slot at a time.
- **Site details stay out of git.** This install's details go in
  `local/README.md` (untracked), never in tracked files: addresses,
  network names, MAC addresses, user names, the time zone, keys. Tracked
  files use placeholders (`WIFI-NAME-HERE`, `USER`) or plain words ("the
  router", "the Pi's reserved address").
- Related work (where the mixer was first measured):
  `~/play/dobro-transcription/dobro-soundfont`.
- Docs here are plain: short sentences, measured numbers with units, no
  marketing.

# Nexus Controller — notes for Claude

Android phone → virtual Xbox 360 / DualShock 4 / Buzz! controller on Windows.
Version 2 is a rewrite of an earlier project; the old code is *not* in this repo.

## Commands

```bat
.venv\Scripts\python -m pytest                 REM server suite, no hardware needed
.venv\Scripts\python tools\smoke_test.py       REM real ViGEmBus + XInput round-trip
.venv\Scripts\python server\run_server.py      REM dashboard
.venv\Scripts\python server\run_server.py --headless --simulate
cd android && gradlew.bat --no-daemon testDebugUnitTest assembleDebug
```

Python 3.14 venv at `.venv`. ViGEmBus **is** installed on this machine, so
`tools/smoke_test.py` really runs. Android SDK at `%LOCALAPPDATA%\Android\Sdk`,
Java 21, Gradle 8.14.3 (wrapper), AGP 8.6.1, Kotlin 1.9.25.

## Architecture

`docs/PROTOCOL.md` is the **normative contract** between the phone and the PC.
Change it first, then both sides. Never let the two implementations drift.

**Pure core / I-O shell.** `protocol.py`, `buzz.py`, `desktop.py` (binding engine)
and `session.py` (limiters) contain no sockets, threads or hardware. `server.py`,
`devices.py`, `system.py`, `app.py` do. That split is why the suite runs in ~2 s
and needs neither a driver nor a phone — keep new logic on the pure side.

```
server/nexus_server/
  protocol.py  wire format, pure          session.py  slots, rate limiting
  devices.py   VirtualPad + ViGEm + Fake  desktop.py  gated mouse/kbd, key binds
  buzz.py      Buzz mapping + ref HID     xinput.py   XInput slot accounting
  server.py    TCP, handshake, discovery  config.py   settings in %APPDATA%
  app.py       dashboard + CLI            web/        dashboard assets, no CDN
android/app/src/main/java/com/nexuscontroller/pad/
  Protocol.kt  pure, mirrors protocol.py  NetworkController.kt  socket + coroutines
  LayoutStore.kt / LayoutFormat.kt        ControllerScreen.kt   the on-screen pad
```

Threads on the server: one accept thread (reserves the slot **atomically**, then
hands off), one per client, one discovery, plus ViGEm callback threads for rumble
and the pywebview UI thread polling `get_state()`.

## Invariants — do not break these

* **Wire Y axis is "+ = up."** The only inversion lives in `DualShock4Pad`; DS4
  hardware runs 0 = up … 255 = down. Do not add a second flip elsewhere.
* **`-128` is never a legal axis value.** Clamp to `-127`.
* **Buzz clients send *semantic* bits** (Red=0x01 … Blue=0x10); the **server**
  translates to XInput. Never pre-map on the client.
* **Layout coordinates are normalised 0–1 fractions of the screen**, addressing a
  component's centre — never pixels. Central config from the PC depends on it.
* **Desktop control is opt-in** and limited to one slot. It is remote keyboard
  access; do not make it default-on or slot-agnostic.
* Slot reservation happens in the accept thread under a lock, *before* the client
  thread starts. The v1 race came from doing it the other way round.
* Every `sendall` on a client socket goes through `PlayerSession.send()` — the
  rumble callback thread and the reader thread both write.

## Gotchas discovered the hard way

* **Windows `SO_REUSEADDR` lets another process steal a bound port** — it is not
  the POSIX meaning. Use `SO_EXCLUSIVEADDRUSE`.
* **Closing a socket with unread data sends RST**, discarding anything just
  written. `_reject()` half-closes and drains so the client actually sees the
  reason.
* **XInput has 4 slots for the whole machine**, shared with physical pads. ViGEm
  creates a 5th device *successfully* but no game can see it — `xinput.py` detects
  this. DS4 is HID and does not consume a slot.
* **`vgamepad`'s `register_notification` compares the callback signature by
  equality** — it must be exactly
  `(client, target, large_motor, small_motor, led_number, user_data)`.
* `DS4_BUTTON_DPAD_*` live in `DS4_DPAD_DIRECTIONS`, not `DS4_BUTTONS`; the DS4
  d-pad is an 8-way hat set via `directional_pad()`.
* In a frozen dataclass, a bare `Final` annotation creates a **field**, not a
  constant. Use `ClassVar`.
* `Modifier.scale`/`rotate` are graphicsLayers and Compose *does* transform
  pointer coordinates, so a gesture inside them receives local-space deltas —
  multiplying pan by scale is correct there.

## Conventions

* Tests are the deliverable, not an afterthought. New protocol behaviour needs a
  golden-byte test on both sides; `tests/test_client_compat.py` decodes the exact
  vectors the Kotlin suite asserts, so drift turns something red. Keep those
  vectors hand-written — never generate them from the code under test.
* No CDN, no remote font, no external script in `web/`. The app must work with the
  network cable unplugged.
* No bare `except:`. Catch specific exceptions; if a broad catch is genuinely
  needed, log it.
* Settings belong in `%APPDATA%\NexusController`, never next to the executable.
* Binaries do not go in git — they are built and attached to GitHub Releases.
* Commit messages in Polish, code and comments in English (the UI is English too).

## Roadmap

**Done** — protocol v2 with token pairing; Xbox/DS4/Buzz emulation; 4 players;
rumble; offline dashboard; key bindings; XInput capacity detection; 442 server
tests + 113 Kotlin tests + hardware smoke test.

**Done, continued** — central configuration (`PROTOCOL.md` §10): live pad preview
on every player card, drag-and-drop designer, controller-type switch from the PC,
named profiles, push-to-one and push-to-all. Single-file `.exe` via
`tools/build_exe.py`, with ViGEmBus fetched at build time and bundled, published by
`.github/workflows/release.yml` on a `v*` tag.

**Next, roughly in order**
1. Profile library UI: duplicate, rename, import/export a layout as a file.
2. Latency readout in the dashboard, from the existing PING/PONG sequence numbers.
3. Snap-to-grid and alignment guides in the designer.
4. Motion/gyro steering as a first-class mode rather than a racing-wheel special case.
5. Signed builds, to stop SmartScreen warning on every download.

**Explicitly not planned**
* Native Buzz HID device emulation — RPCS3 drives buzzers from ordinary pads, so
  it buys nothing. `buzz.py` keeps a tested reference implementation in case that
  ever changes.
* Bluetooth. The v1 service was orphaned and its `uses-feature required="true"`
  blocked installation on non-BLE devices.
* iOS. ViGEmBus is Windows-only anyway, and this is a LAN tool.

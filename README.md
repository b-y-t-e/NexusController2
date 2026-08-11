# Nexus Controller

Turn an Android phone into a virtual **Xbox 360**, **DualShock 4** or **Buzz! (PS3)**
controller for Windows, over Wi-Fi or USB.

<p align="center"><img src="docs/logo.png" width="140" alt=""></p>

---

## What it does

* **Three controller types**, switchable from the phone without restarting anything:
  * **Xbox 360** — a real XInput pad, works with everything on Windows.
  * **DualShock 4** — for games that prefer a PlayStation pad (correct 8-way hat and lightbar).
  * **Buzz! (PS3)** — a big red buzzer plus four coloured answer buttons, mapped exactly the
    way RPCS3 expects. Up to four phones become buzzers 1–4.
* **Four players** at once, one virtual pad each.
* **Rumble** relayed from the game back to the phone.
* **Pairing by QR code** with a rotating token — nothing on your network can connect without it.
* **Central configuration.** See every connected pad live on the PC and rearrange
  its buttons from there — drag them around, resize, rotate, then push the layout to
  one phone or to all of them at once. Save layouts as named profiles. You never have
  to touch the phones.
* **Optional** mouse & keyboard control, off by default.
* Works **completely offline**. The dashboard ships its own assets.

## Requirements

| | |
| --- | --- |
| PC | Windows 10/11, Python 3.10+ |
| Driver | [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) |
| Phone | Android 9 (API 28) or newer |
| Network | Both devices on the same Wi-Fi, or a USB cable |

## Install (PC)

**The easy way.** Download `NexusController.exe` from the
[latest release](../../releases/latest) and run it. No Python, no terminal, nothing
to configure. If the ViGEmBus driver is missing the app says so and offers an
**Install driver** button — the installer is bundled inside the executable, so it
works offline. Reboot once afterwards.

Windows SmartScreen will warn about an unsigned download the first time: *More
info → Run anyway*.

**From source**, if you would rather:

```bat
git clone https://github.com/b-y-t-e/NexusController2 NexusController
cd NexusController

python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
RunServer.bat
```

Either way: press **START SERVER**, then scan the QR code with the phone app.

Administrator rights are only needed for two optional things: the automatic firewall
rule, and the mouse/keyboard feature. If you would rather not run the server elevated,
run `tools\add_firewall_rule.bat` once as Administrator and start the server normally.

### Command line

```bat
.venv\Scripts\python server\run_server.py            REM dashboard
.venv\Scripts\python server\run_server.py --headless REM no window
.venv\Scripts\python server\run_server.py --simulate REM no ViGEmBus needed
```

## Install (Android)

Open `android/` in Android Studio, let Gradle sync, and run. Or build from the command line:

```bat
cd android
gradlew.bat assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`.

## Connecting

**Wi-Fi** — start the server, open the app, tap *Scan QR*, point it at the dashboard.
Auto-discovery also finds servers on the same subnet (UDP broadcast on port 6001).

**USB** — enable USB debugging, plug the phone in, and start the server; it runs
`adb reverse tcp:6000 tcp:6000` for you. In the app choose *USB* — it connects to
`127.0.0.1`, which the reverse tunnel forwards to the PC.

## Configuring pads from the PC

Each player card in the dashboard shows a **live thumbnail** of that phone's actual
layout, with buttons lighting up as they are pressed — so you can see at a glance
what is connected and what it looks like.

**Layout** opens the designer:

* drag any control to move it, or select it and use the sliders to resize and rotate;
* switch the controller type (Xbox 360 / DualShock 4 / Buzz) — the phone reconnects
  itself to announce the change;
* changes go to the phone as you make them (turn off *Apply changes live* if you
  would rather push explicitly);
* **Save** a layout as a named profile, then **Push to all connected** to put it on
  every phone at once.

Positions are stored as fractions of the screen rather than pixels, so one profile
lands correctly on a small phone and a tablet alike.

## Buzz! mode

RPCS3 emulates the Buzz! dongle itself (USB device `054C:0002`) and drives each of its
four buzzers from an ordinary pad — buzzer *N* reads the pad of player *N*. So this app
presents a normal virtual Xbox 360 pad using RPCS3's default `buzz.yml` bindings:

| Buzz button | RPCS3 binding | XInput button |
| --- | --- | --- |
| Red (buzz) | `R1` | RB |
| Yellow | `Cross` | A |
| Green | `Circle` | B |
| Orange | `Square` | X |
| Blue | `Triangle` | Y |

In RPCS3 set **Settings → I/O → Emulated Buzz devices → 1 controller**, then connect one
phone per player. No extra driver is required. The real dongle's HID report format is
implemented in `server/nexus_server/buzz.py` and covered by tests, for reference.

## How many controllers?

Four players connect to the server at once, but **Windows itself exposes only four
XInput slots for the whole machine**, and physical controllers share them. So:

* 4 phones in Xbox/Buzz mode → 4 XInput pads, verified as four independent devices.
* 1 physical Xbox pad plugged in → only **3** phones will be visible to games.
* A fifth XInput device is created without any error by the driver but is invisible to
  every game. The server detects this and shows a warning in the dashboard instead of
  letting you hunt a phantom bug.
* **DualShock 4 mode does not use an XInput slot** — it is a HID device. If you run out
  of XInput slots, switch a phone to DS4 mode.

## Security

This server accepts input that moves your mouse and presses buttons, so it is built to
be uninteresting to anything else on the network:

* Every client must present a **128-bit pairing token**, compared in constant time. The
  token rotates on each start unless you pin it, and is only ever shown as a QR code.
* Five failed handshakes from one address earn a **60-second block**.
* The server binds **one chosen interface**, not `0.0.0.0`.
* The firewall rule it adds is scoped to the **private** profile and is **removed on exit**.
* **Mouse and keyboard control is off by default**, must be enabled explicitly, and only
  one player slot may use it.
* Input is rate-limited per connection.

Even so: treat it like any LAN service. Do not run it on a network you do not trust.

## Development

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                    REM 431 tests, no hardware needed
.venv\Scripts\python tools\smoke_test.py          REM real ViGEmBus + XInput round-trip
cd android && gradlew.bat testDebugUnitTest   REM 52 Kotlin tests
```

`tests/test_client_compat.py` decodes the exact byte vectors asserted by the Kotlin
suite, so the two implementations cannot drift apart without a test going red.

The pytest suite (431 tests) runs against a fake pad backend, so it needs neither ViGEmBus nor a
phone. `tools/smoke_test.py` is the one that touches real hardware: it creates an actual
virtual pad and reads it back through the Windows XInput API.

### Layout

```
server/nexus_server/
  protocol.py   wire format — pure functions, no I/O
  padconfig.py  pad layout documents pushed to phones
  devices.py    virtual pads (ViGEm-backed, plus a fake for tests)
  buzz.py       Buzz mapping and the reference HID report
  server.py     TCP server, handshake, discovery
  session.py    player slots, rate limiting
  desktop.py    gated mouse/keyboard, pad-button → key bindings
  config.py     settings in %APPDATA%\NexusController
  app.py        dashboard and CLI
  web/          dashboard assets (no CDN, no network)
tools/build_exe.py  builds the single-file .exe, bundling ViGEmBus
android/        Jetpack Compose client
docs/PROTOCOL.md  the contract between the two
tests/          pytest suite
```

Settings live in `%APPDATA%\NexusController\settings.json`, never inside the install
directory, so the app keeps working from `Program Files`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `SIMULATION` badge in the dashboard | ViGEmBus is not installed. Install it and reboot. |
| Phone cannot see the server | Run `tools\add_firewall_rule.bat` as Administrator. Check both devices are on the same network and that the router does not use AP isolation. |
| "Invalid pairing token" | The token rotated when the server restarted. Scan the QR code again, or tick *Keep the same token between restarts*. |
| Buzz buttons do nothing in RPCS3 | Set **Emulated Buzz devices** to *1 controller* in RPCS3's I/O settings. |
| Input lag | Prefer 5 GHz Wi-Fi, or use USB mode. |
| Mouse mode does nothing | Enable *Desktop control* in the dashboard and make sure the right player slot is selected. |

## Acknowledgements

Version 2 is a rewrite built on the original **Nexus Controller** by Abhishek Singh.
The MIT notice for that work is preserved in [LICENSE](LICENSE).

## License

MIT — see [LICENSE](LICENSE).

# Nexus Controller

Turn an Android phone into a virtual **Xbox 360**, **DualShock 4** or **Buzz! (PS3)**
controller for Windows, over Wi-Fi or USB.

<p align="center"><img src="docs/logo.png" width="140" alt=""></p>

---

## What it does

* **Three controller types**, switchable from the phone *or* from the PC, without
  restarting anything:
  * **Xbox 360** — a real XInput pad, works with everything on Windows.
  * **DualShock 4** — for games that prefer a PlayStation pad (correct 8-way hat and lightbar).
  * **Buzz! (PS3)** — a big red buzzer plus four coloured answer buttons, mapped exactly the
    way RPCS3 expects. Up to four phones become buzzers 1–4.
* **Eight players** at once, one virtual pad each — though Windows only shows four
  *XInput* pads to games, so past that point use DualShock 4 mode (see below).
* **Rumble** relayed from the game back to the phone.
* **Pairing by QR code** with a 128-bit token — nothing on your network can connect
  without it. The token is kept between restarts by default, so a paired phone stays
  paired; untick *Keep the same token* to rotate it on every start instead.
* **Central configuration.** See every connected pad live on the PC and rearrange
  its buttons from there — drag them around, resize, rotate, then push the layout to
  one phone or to all of them at once. Save layouts as named profiles. You never have
  to touch the phones.
* **Optional** mouse & keyboard control, off by default.
* Works **completely offline**. The dashboard ships its own assets.

## Requirements

| | |
| --- | --- |
| PC | Windows 10 or 11 |
| Python | only when running from source — 3.10 or newer. The released `.exe` needs none |
| Driver | [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest), bundled inside the released `.exe` |
| Phone | Android 9 (API 28) or newer — or Android 5.0 with the *legacy* APK |
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

### The firewall

Over **Wi-Fi**, Windows has to let the phone in: inbound **TCP 6000** and **UDP 6001**.
Over **USB** nothing is needed — the phone dials `127.0.0.1` through `adb reverse`.

You do not have to configure that by hand. If the ports are closed the dashboard says
so and offers an **Open port** button; Windows asks for Administrator consent once and
the rules are added. They cover just those two ports, inbound, on the **private** network
profile.

Windows files most Wi-Fi networks — a phone hotspot included — under *public*, and a
private-profile rule does nothing there. The dashboard says so when it applies, and
offers a second button, **Include public networks**, which widens the rule. That is
never done automatically: a public-profile rule applies on every public network the
machine ever joins.

A server started elevated adds the private rules itself and removes them again on exit.
Rules you asked for with the button are yours and stay.

If you would rather do it yourself, `tools\add_firewall_rule.bat` run once as
Administrator does exactly the same thing. Administrator rights are needed for nothing
else except the optional mouse/keyboard feature.

### Command line

```bat
.venv\Scripts\python server\run_server.py            REM dashboard
.venv\Scripts\python server\run_server.py --headless REM no window
.venv\Scripts\python server\run_server.py --simulate REM no ViGEmBus needed
```

## Install (Android)

Download `NexusController.apk` from the [latest release](../../releases/latest) and
install it — Android will ask you to allow installs from this source.

If the phone is too old for it, take `NexusController-legacy.apk` instead: the same
app, built to install back to **Android 5.0**. It exists because a room of four Buzz
buzzers is usually four phones out of a drawer, not four current ones.

To build it yourself, open `android/` in Android Studio, or from the command line:

```bat
cd android
gradlew.bat assembleModernDebug assembleLegacyDebug
```

They land in `android/app/build/outputs/apk/modern/debug/` and `.../legacy/debug/`.

To build *both* halves of a release the way a tag does — tests, the `.exe`, the
APK, and a `SHA256SUMS.txt` over them — run one script from the repository root:

```bat
.venv\Scripts\python build_release.py
```

Everything ends up in `release/`. `--exe-only`, `--apk-only` and
`--skip-tests` narrow it down while iterating.

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

Eight players connect to the server at once, but **Windows itself exposes only four
XInput slots for the whole machine**, and physical controllers share them. So:

* 4 phones in Xbox/Buzz mode → 4 XInput pads, verified as four independent devices.
* Beyond four, an Xbox/Buzz phone still connects and still gets its own player slot,
  but games will not see it — put those phones in DualShock 4 mode.
* 1 physical Xbox pad plugged in → only **3** phones will be visible to games.
* A fifth XInput device is created without any error by the driver but is invisible to
  every game. The server detects this and shows a warning in the dashboard instead of
  letting you hunt a phantom bug.
* **DualShock 4 mode does not use an XInput slot** — it is a HID device. If you run out
  of XInput slots, switch a phone to DS4 mode.

## Security

This server accepts input that moves your mouse and presses buttons, so it is built to
be uninteresting to anything else on the network:

* Every client must present a **128-bit pairing token**, compared in constant time. It
  is only ever shown as a QR code, and is kept between restarts so paired phones stay
  paired; untick *Keep the same token* to have a fresh one on every start.
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
.venv\Scripts\python -m pytest                    REM 442 tests, no hardware needed
.venv\Scripts\python tools\smoke_test.py          REM real ViGEmBus + XInput round-trip
cd android && gradlew.bat testDebugUnitTest   REM 113 Kotlin tests
```

`tests/test_client_compat.py` decodes the exact byte vectors asserted by the Kotlin
suite, so the two implementations cannot drift apart without a test going red.

The pytest suite (442 tests) runs against a fake pad backend, so it needs neither ViGEmBus nor a
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
  xinput.py     XInput slot accounting (see "How many controllers?")
  netinfo.py    local address discovery
  system.py     firewall rules and adb reverse
  config.py     settings in %APPDATA%\NexusController
  app.py        dashboard and CLI
  web/          dashboard assets (no CDN, no network)
build_release.py         builds .exe + .apk into release/, as a tag would
tools/build_exe.py       builds the single-file .exe, bundling ViGEmBus
tools/smoke_test.py      real ViGEmBus + XInput round-trip
android/                 Jetpack Compose client
docs/PROTOCOL.md         the contract between the two
tests/                   pytest suite
.github/workflows/       tagged builds, published to Releases
```

Settings live in `%APPDATA%\NexusController\settings.json`, never inside the install
directory, so the app keeps working from `Program Files`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `SIMULATION` badge in the dashboard | ViGEmBus is not installed. Click **Install driver** in the yellow banner (the released `.exe` bundles the installer), then reboot. Running from source? Install it from the link above. |
| Phone cannot see the server | If the dashboard shows the firewall banner, press **Open port**. Otherwise check both devices are on the same network and that the router does not use AP isolation. |
| Layout pushed from the PC did not arrive | The phone must be connected — the dashboard reports "Player not connected". Check the player card shows a live thumbnail. |
| "Invalid pairing token" | The token changed — you pressed **New**, or unticked *Keep the same token between restarts*. Scan the QR code again. |
| Buzz buttons do nothing in RPCS3 | Set **Emulated Buzz devices** to *1 controller* in RPCS3's I/O settings. |
| Input lag | Prefer 5 GHz Wi-Fi, or use USB mode. |
| Mouse mode does nothing | Enable *Desktop control* in the dashboard and make sure the right player slot is selected. |

## Acknowledgements

Version 2 is a rewrite built on the original **Nexus Controller** by Abhishek Singh.
The MIT notice for that work is preserved in [LICENSE](LICENSE).

## License

MIT — see [LICENSE](LICENSE).

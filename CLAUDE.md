# Nexus Controller — notes for Claude

Android phone → virtual Xbox 360 / DualShock 4 / Buzz! controller on Windows.
Version 2 is a rewrite of an earlier project; the old code is *not* in this repo.

## Commands

```bat
.venv\Scripts\python -m pytest                 REM server suite, no hardware needed
.venv\Scripts\python tools\smoke_test.py       REM real ViGEmBus + XInput round-trip
.venv\Scripts\python server\run_server.py      REM dashboard
.venv\Scripts\python server\run_server.py --headless --simulate
cd android && gradlew.bat --no-daemon testModernDebugUnitTest assembleModernDebug
cd android && gradlew.bat --no-daemon lintModernDebug lintLegacyDebug  REM API levels
.venv\Scripts\python build_release.py        REM tests + .exe + 2x .apk -> release\
.venv\Scripts\python publish_release.py patch  REM bump + build + commit + tag + push
.venv\Scripts\python publish_release.py minor --dry-run
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
  autostart.py HKCU Run key               tray.py     notification-area icon
  shortcuts.py Start menu / desktop .lnk
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
* Slot reservation is an **atomic compare-and-set** under the allocator lock
  (`SlotAllocator.acquire`), and happens **only after a valid HELLO**. The v1 race
  came from a non-atomic check-then-set in the client thread; reserving before the
  handshake instead let an unauthenticated peer hold a slot for the whole timeout.
  The accept thread only turns a connection away early when *no* slot is free.
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
* **A running `.exe` cannot be overwritten or deleted on Windows, but it can be
  renamed.** That is the whole trick behind updating in place: rename the old one
  aside, move the new one in, start it, quit; the leftover is deleted on the next
  start, when nothing holds it. `os.access` is no use for asking whether the
  directory allows this — under UAC it answers about the DACL, not about the
  token — so `updates.writable()` finds out by creating a file.
* **`PackageInstaller` needs a `PendingIntent` that is `FLAG_MUTABLE` from API
  31**, because the system fills the result into it; on 31 and 32 creating one
  with neither mutability flag throws. It also does not follow GitHub's redirect
  to its object store by itself — `HttpURLConnection` stops at a cross-host
  redirect, so `Updater` follows it by hand, https only.
* Compose's `BuildConfig` is **off by default in AGP 8**; `buildConfig = true`
  in `buildFeatures` is what makes `VERSION_NAME` and `FLAVOR` exist.
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
* **Lint both flavours** (`lintModernDebug lintLegacyDebug`) — it is part of
  `build_release.py` and of CI. `legacy` starts at API 21 and `modern` at 28,
  and a call to a newer API compiles, passes the unit tests and works on the
  phone in your hand, then throws `NoClassDefFoundError` on an older one —
  an `Error`, so no `catch (e: Exception)` around it ever sees it.
* No bare `except:`. Catch specific exceptions; if a broad catch is genuinely
  needed, log it.
* Settings belong in `%APPDATA%\NexusController`, never next to the executable.
* Binaries do not go in git — they are built and attached to GitHub Releases.
* **Releases ship signed `assembleRelease` APKs, never debug ones.** A debug APK
  is signed with `~/.android/debug.keystore`, which the CI runner *generates fresh
  on every run* — so each release carried a different signature and Android
  refused to update in place (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`); the only way
  forward was uninstall, losing every layout. The release key lives in
  `%APPDATA%\NexusController\signing\nexus-release.jks`, is pointed at by the
  gitignored `android/keystore.properties`, and reaches CI as four repository
  secrets (`NEXUS_KEYSTORE_BASE64` and friends). **Back that file up**: losing it
  strands every installed copy on the version it has. Its certificate is
  `13288e7a…c72feb`, pinned as `build_release.py:RELEASE_CERT_SHA256` — checked
  against every APK by the local build *and* by the workflow, which reads that
  same constant rather than keeping a second copy of it.
* Commit messages in Polish, code and comments in English (the UI is English too).

## Roadmap

**Done** — protocol v2 with token pairing; Xbox/DS4/Buzz emulation; 8 players
(`protocol.MAX_PLAYERS`; only 4 of them can be XInput-backed — see `xinput.py`);
rumble; offline dashboard; key bindings; XInput capacity detection; 832 server
tests + 200 Kotlin tests + hardware smoke test.

**Done, continued** — central configuration (`PROTOCOL.md` §10): live pad preview
on every player card, drag-and-drop designer, controller-type switch from the PC,
named profiles, push-to-one and push-to-all. Single-file `.exe` via
`tools/build_exe.py`, with ViGEmBus fetched at build time and bundled, published by
`.github/workflows/release.yml` on a `v*` tag. `build_release.py` in the root runs
the same steps locally and collects both artefacts into `release/` (gitignored).

**Done, and worth knowing about** — in-app update on both sides, against the
GitHub releases API (`updates.py`, `UpdateCheck.kt` / `Updater.kt`). The deciding
half is pure on both sides and the suites never open a socket. Shared rules: an
asset URL is used only if it starts with our own `releases/download/` prefix; the
download is checked against the release's `SHA256SUMS.txt`; versions compare
numerically, never as text; a redirect is followed only while it stays on https,
because these bytes become the program that runs next and the checksum travels
the same road; the download is **streamed to a file** and never held in memory,
on both sides, whatever it wrote is deleted if anything goes wrong, and on the PC
it is `fsync`ed before the rename — a rename can be committed while the contents
behind it are not, and this one puts the file under the name of the app; and
`/releases/latest` answering 404 means "no release yet", not an error — same as
being offline, which is a normal state for this app and never raises a dialog.

The update state on the PC is `updates.UpdateState` — a small state machine with
no I/O in it, on the pure side for the same reason as everything else there: its
rules *are* the correctness of the feature, and they are driven by tests directly
rather than through a dashboard, a thread and a fake GitHub. Two owners: the
check worker may only write while the state is still `checking` *and* its
generation is current, and `install_update` takes it with a compare-and-set. Neither is
politeness — a check landing over `installing` re-enables the button under a
running download, and two installs in `install_staged()` can leave the directory
with no `.exe` at all. `installed` is terminal for the process: the swap has
happened but this build is still the old one and still reports the old version,
so anything started from there would offer the release to itself and the second
install would take the *new* build for the old one.

**Done, and worth knowing about** — starting with Windows and the tray icon.
The login entry is the per-user `HKCU\…\CurrentVersion\Run` key and nothing else:
no service, no scheduled task, no Start-up shortcut, because those need
elevation or a file the user did not put there, and the Run key is the one place
Task Manager's Start-up tab shows — so it can be turned off without this app.
The registry *is* the state; nothing about it is kept in `settings.json`, or the
two would disagree the first time somebody used that tab. Only a frozen build can
register itself, and the command is quoted (`"C:\Program Files\…"` unquoted is a
program called `C:\Program`, and the entry then fails silently at every login).
The identity of an entry is the *program* it names, not the exact text — flags
change between builds — and it governs both sides: an entry starting another copy
is neither reported as ours nor deleted by our switch. The entry passes
`--minimized`, because logging in is not asking to be shown a
window — but it is a request, not a promise: `tray.start_hidden()` honours it only
when the icon actually came up, so the app can never start with neither window
nor icon, which is exactly the trap `--headless` at login would have been.

The server comes back up by itself when that is where the last session left it
(`settings.server_running`, restored by `Api.resume_server()` before the window
is created). The flag is the *user's last decision*, not a snapshot: pressing
Stop clears it and nothing else does, so quitting, an update, or Windows shutting
down mid-game all leave it set and the phone finds the server there again. A
restore that cannot bind — a dock unplugged, a port taken during boot — says so
in the log and leaves the flag alone, because the dock comes back tomorrow.

`bind_ip` may be `0.0.0.0` (`netinfo.ALL_INTERFACES`, "All interfaces" on the
page) for a PC that is on two networks at once. The wildcard is only ever a
*listening* address: everything that names the server to a phone — the QR code,
the pairing line, the card next to it — goes through `netinfo.advertised_ip()`,
which answers `primary_ip()` there. The firewall check reads the bound address,
and `network_category_for("0.0.0.0")` answering None lands on the cautious
"public" branch by itself, which is right: the listener really is on every
network, including the ones the private-profile rule does not cover.

`shortcuts.py` puts a `.lnk` in the Start menu and on the desktop, through
`WScript.Shell` (PowerShell, no new dependency). The Start-menu one is not
decoration: it is what makes Windows Search find a downloaded `.exe`, and what
"Pin to Start" needs to pin. Same two rules as autostart — the file system *is*
the state, and identity is the program a shortcut starts, so another copy's
`.lnk` is neither reported as ours nor deleted by our switch. The desktop folder
is **asked of Windows** (`GetFolderPath('Desktop')`), never assumed to be
`~/Desktop`: OneDrive moves it and localises the name (`…\OneDrive\Pulpit` on
this machine). Every call there is a PowerShell process, so both places are
answered in one trip and the folder is looked up once per run.

**The release runs the `.exe` it just built** (`--selftest`: imports the GUI
backend, checks the dashboard assets, exits — no window, so it works on a
runner). Four releases shipped before a bundle that could not import its own
backend was found *by a user*, from a crash dialog: pywebview puts three native
runtime folders on `PATH` when it imports WinForms — `win-arm64` among them, on
an x64 machine — and a bundle missing any of them dies with "Cannot find
win-arm64" before a line of this app runs. Hence also `--collect-all webview`
rather than trusting PyInstaller's hook. No test in either suite can see this:
they run from the source tree, where those folders are simply there.

**Cursor movement injects a relative `SendInput`, not a `SetCursorPos`.**
pynput's relative move is read-modify-write of the global cursor position, a
hundred times a second, from a socket thread: it reads a position the last write
has not landed in yet — the pointer snaps back over ground it covered — and it
fights the mouse on the desk. `desktop.relative_mover()` builds the ctypes call
once; `ULONG_PTR` must be pointer-sized or the struct is mis-sized and every
event is rejected silently. `ERROR_ACCESS_DENIED` from it is **not** a broken
backend: UIPI refuses injected input while the focused window belongs to a
program running as administrator, and it starts working again when that window
loses focus — so it falls back for that message only, and says so at most every
30 seconds. Movement itself is chunked on the phone (`DeltaAccumulator`): a
flick is more than one signed byte, and the whole distance has to leave in one
call, because if the finger stops there is no next pointer event to carry the
rest — it would ride out at the start of the next gesture instead.

Tilt yields to the finger. Both reach the same cursor in trackpad mode, and
holding a phone to swipe on it tilts the phone, so gyro steering was adding
drift to every stroke; `GYRO_YIELD_SECONDS` after any MOUSE movement the gyro
half of `_apply_mouse_mode` stands down and re-latches its centre afterwards.

Closing the window hides it to the tray instead of quitting, unless the setting
says otherwise or the icon did not start — `tray.decide_close()` is the whole
rule and it is pure. "Did not start" means pystray's own `setup=` callback ran
and `visible = True` came back, not that a thread was started: that answer
decides whether a `--minimized` login shows a window at all, and a thread is not
an icon anybody can click. Passing a custom `setup=` *replaces* the one pystray
uses to show the icon, so ours has to set `visible` itself. `Api.quit()` is the
way out that really ends the process: the tray's own Quit, and the last step of
an update, which must not leave the old build in the tray holding the port the
new one wants. `pystray` is a dependency
the app survives without: no icon means X quits, and the dashboard says so rather
than showing a switch that lies.

**Next, roughly in order**
1. Profile library UI: duplicate, rename, import/export a layout as a file.
2. Latency readout in the dashboard, from the existing PING/PONG sequence numbers.
3. Snap-to-grid and alignment guides in the designer.
4. Motion/gyro steering as a first-class mode rather than a racing-wheel special case.
5. Authenticode signing for the `.exe`, to stop SmartScreen warning on every
   download. Unlike the APK key this one has to be bought, and it is the only
   remaining piece of "signed builds".

**Explicitly not planned**
* Native Buzz HID device emulation — RPCS3 drives buzzers from ordinary pads, so
  it buys nothing. `buzz.py` keeps a tested reference implementation in case that
  ever changes.
* Bluetooth. The v1 service was orphaned and its `uses-feature required="true"`
  blocked installation on non-BLE devices.
* iOS. ViGEmBus is Windows-only anyway, and this is a LAN tool.

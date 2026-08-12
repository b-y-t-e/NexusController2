# Nexus Controller wire protocol — version 2

Transport: **TCP**, default port **6000**, `TCP_NODELAY` enabled on both ends.
All multi-byte integers are **big-endian**. Signed 8-bit values are two's complement.

Every message starts with a one-byte **opcode**. Payload length is fixed per opcode
(except `TEXT`, which carries an explicit length byte), so the stream is
self-framing and a peer may always skip a message it does not understand only if
it knows the length — therefore **unknown opcodes are a fatal protocol error** and
the connection is closed.

---

## 1. Connection lifecycle

```
client                                  server
  |  TCP connect                          |
  |-------------------------------------->|
  |  HELLO (0x10)                         |
  |-------------------------------------->|
  |                     WELCOME (0x11)    |   accepted
  |<--------------------------------------|
  |                  or REJECT (0x1F)     |   refused, server closes
  |<--------------------------------------|
  |  INPUT / PING / TEXT / MOUSE ...      |
  |<------------------------------------->|
```

The server **must** receive a valid `HELLO` as the first message. Any other opcode
before the handshake is answered with `REJECT(REASON_UNAUTHENTICATED)` and the
connection is closed. The server applies a handshake timeout (default 5 s).

---

## 2. Client → server messages

### `0x10` HELLO

| offset | size | field | notes |
| --- | --- | --- | --- |
| 0 | 1 | opcode `0x10` | |
| 1 | 1 | protocol version | must be `0x02` |
| 2 | 1 | device type | see §4 |
| 3 | 1 | token length `T` | 0–64 |
| 4 | T | token | ASCII, compared in constant time |
| 4+T | 1 | name length `N` | 0–32 |
| 5+T | N | display name | UTF-8, sanitised by server |

### `0x01` INPUT — 16-byte payload

| offset | size | type | field |
| --- | --- | --- | --- |
| 0 | 1 | int8 | left stick X (`-127`…`127`, `0` = centre, **+ = right**) |
| 1 | 1 | int8 | left stick Y (`0` = centre, **+ = up**) |
| 2 | 1 | int8 | right stick X |
| 3 | 1 | int8 | right stick Y (**+ = up**) |
| 4 | 1 | uint8 | buttons low (§3) |
| 5 | 1 | uint8 | buttons high (§3) |
| 6 | 1 | uint8 | left trigger `0`…`255` |
| 7 | 1 | uint8 | right trigger `0`…`255` |
| 8 | 2 | int16 | gyro roll |
| 10 | 2 | int16 | gyro pitch |
| 12 | 1 | uint8 | flags: bit0 = mouse mode, bit1 = gyro valid |
| 13 | 3 | — | reserved, must be zero |

> **Y axis is "positive = up" on the wire.** This matches XInput natively; no side
> performs a hidden inversion. Clients whose touch coordinates grow downwards must
> negate before sending.

`-128` is not a legal axis value; the server clamps it to `-127`.

### `0x02` TEXT — keyboard injection

| offset | size | field |
| --- | --- | --- |
| 0 | 1 | opcode `0x02` |
| 1 | 1 | byte length `L` (0–255) |
| 2 | L | UTF-8 text |

`\b` is typed as Backspace, `\n` as Enter. Honoured **only** when the server's
*desktop control* feature is enabled and only for the slot holding the desktop
lock (§6).

### `0x04` MOUSE — `dx:int8, dy:int8, buttons:uint8` (bit0 = left, bit1 = right, bit2 = middle)

### `0x05` SCROLL — `dx:int8, dy:int8`

### `0x06` CONFIG — the client reports its current configuration

| offset | size | field |
| --- | --- | --- |
| 0 | 1 | opcode `0x06` |
| 1 | 2 | uint16 body length `L` (max 16384) |
| 3 | L | UTF-8 JSON, see §10 |

Sent once immediately after `WELCOME`, and again whenever the user changes
anything on the phone. This is what lets the PC show what each pad looks like.

### `0xF0` PING — `seq:uint32`

---

## 3. Button bit layout

`buttons_low` — meaning depends on device type (§4).

| bit | Xbox 360 / DualShock 4 | Buzz |
| --- | --- | --- |
| `0x01` | A / Cross | **Red** (buzzer) |
| `0x02` | B / Circle | **Yellow** |
| `0x04` | X / Square | **Green** |
| `0x08` | Y / Triangle | **Orange** |
| `0x10` | LB / L1 | **Blue** |
| `0x20` | RB / R1 | — |
| `0x40` | Back / Share | — |
| `0x80` | Start / Options | — |

`buttons_high` — identical for every device type.

| bit | meaning |
| --- | --- |
| `0x01` | left stick click (L3) |
| `0x02` | right stick click (R3) |
| `0x04` | D-pad up |
| `0x08` | D-pad down |
| `0x10` | D-pad left |
| `0x20` | D-pad right |
| `0x40` | Guide / PS button |
| `0x80` | reserved |

> Protocol v1 stole `buttons_high` bits `0x40`/`0x80` for mode flags, which made the
> Guide button unreachable. v2 moves those flags to the dedicated flags byte.

---

## 4. Device types

| value | name | emulated device on the PC |
| --- | --- | --- |
| `0x00` | `XBOX360` | ViGEm virtual Xbox 360 pad (XInput) |
| `0x01` | `DUALSHOCK4` | ViGEm virtual DualShock 4 pad |
| `0x02` | `BUZZ` | ViGEm virtual Xbox 360 pad with the RPCS3 Buzz mapping |

### Buzz mapping

RPCS3 emulates the Buzz! dongle as a USB device (`VID 054C` / `PID 0002`,
*"Logitech Buzz(tm) Controller V1"*) but drives each of its four buzzers from an
ordinary pad — buzzer *N* reads the pad of player *N*. The defaults in RPCS3's
`buzz_config.h` are:

| Buzz button | RPCS3 pad button | XInput button we emit |
| --- | --- | --- |
| Red (buzz) | `R1` | `RIGHT_SHOULDER` |
| Yellow | `Cross` | `A` |
| Green | `Circle` | `B` |
| Orange | `Square` | `X` |
| Blue | `Triangle` | `Y` |

So a phone in Buzz mode sends *semantic* buzz bits (§3) and the **server**
translates them to XInput. Four phones in Buzz mode occupy four slots and appear
to RPCS3 as players 1–4, i.e. buzzers 1–4 of one dongle. In RPCS3 set
**Settings → I/O → Emulated Buzz devices = 1 controller**.

The hardware/RPCS3 HID report format is implemented in `nexus_server.buzz` for
reference and is covered by tests, but is **not** used at runtime — no HID driver
is required.

---

## 5. Server → client messages

| opcode | payload | meaning |
| --- | --- | --- |
| `0x11` WELCOME | `slot:uint8, features:uint8` | handshake accepted; `slot` is 0-based and below the server's slot count (`MAX_PLAYERS`, currently 8). features bit0 = rumble available, bit1 = LED available |
| `0x1F` REJECT | `reason:uint8` | handshake refused, server closes immediately |
| `0x03` RUMBLE | `large:uint8, small:uint8` | force feedback, `0`…`255` |
| `0x12` LED | `r:uint8, g:uint8, b:uint8` | DS4 lightbar colour / Buzz lamp (non-zero = lamp on) |
| `0x13` SET_CONFIG | `length:uint16, json:UTF-8` | push a configuration to the phone (§10) |
| `0xF1` PONG | `seq:uint32` | echoes the `PING` sequence number |

Reject reasons:

| value | meaning |
| --- | --- |
| `0x01` | unsupported protocol version |
| `0x02` | invalid token |
| `0x03` | no free player slot **right now** — every slot taken, the handshake queue full, or the server stopping. Transient: a client should keep retrying |
| `0x04` | malformed handshake — the HELLO arrived and was wrong. A connection that sends nothing at all is closed without a REJECT: there is no malformed message to complain about, and nobody listening |
| `0x05` | unauthenticated (non-HELLO sent first) |
| `0x06` | rate limited — too many *failed credentials* from this address. A client may treat this as final and stop retrying, so the server must never use it for a transient condition |

---

## 6. Desktop control (mouse & keyboard)

`TEXT`, `MOUSE` and `SCROLL` let a client move the PC's cursor and type. This is a
remote-control capability, so:

* it is **disabled by default**;
* it must be enabled explicitly in the server dashboard;
* only **one** slot holds the *desktop lock* at a time (by default slot 0);
* when disabled, those opcodes are parsed and discarded, never executed.

---

## 7. Discovery — UDP 6001

Client broadcasts the ASCII request:

```
NEXUSPAD_DISCOVER_V2
```

Server replies to the sender:

```
NEXUSPAD_SERVER_V2|<display name>|<tcp port>|<0|1 token required>
```

`|` is forbidden inside the display name. Discovery only answers while the TCP
server is running.

---

## 8. Pairing token & QR code

On start the server generates a random 128-bit token, rendered as 32 lowercase hex
characters, and shows it as a QR code containing exactly:

```
NEXUSPAD2:<ip>:<port>:<token>
```

The token is **kept across restarts by default**, so a phone that has been paired
stays paired; the user can ask for it to be rotated on every start instead. Clients
store the token per server IP, which is what makes reconnecting automatic — and is
why rotating it means rescanning the QR code on every one of them.

`<token>` is 1–64 hex characters, **or empty** when the user has turned pairing
off. An empty token is a valid payload and means "this server accepts any HELLO":
a client must accept such a QR code rather than treat it as garbage. What it then
puts in the token field of its HELLO is its own business — the server ignores the
field entirely while pairing is off, so a client is free to send an empty token or
to reuse one it remembers for that address. An empty token must not be confused
with a malformed payload: three colon-separated fields (`NEXUSPAD2:<ip>:<port>`)
is malformed, four with the last one empty is not.

---

## 9. Rate limiting

* Max **1000 INPUT messages/second** per connection; excess is dropped and counted.
  A client should send on *change* rather than on a timer — a pad nobody is
  touching has nothing to report — with a slow heartbeat so a lost packet
  cannot strand the server on a stale state.
* Max **5 failed handshakes per source IP per 60 s**, then `REJECT(0x06)` without
  even parsing the token. That budget is for *wrong credentials* — a bad token, a
  bad version, a malformed HELLO.
* Silence is counted separately and far more loosely (**30 per 60 s**): a
  connection held open without sending a HELLO is indistinguishable from a phone
  whose Wi-Fi dropped mid-handshake, and clients retry every few seconds, so
  spending the credentials budget on it would let a bad minute of network lock a
  legitimate phone out. A connection closed again straight away costs nothing at
  all, because port scans and health checks look exactly like that.
* A **player slot is reserved only after a valid HELLO**, so an unauthenticated
  connection cannot occupy one. The server refuses with `REJECT(0x03)` before the
  handshake when no slot is free at all, and likewise when too many handshakes are
  already in flight — that is a transient condition, so it must **not** be
  reported as `REJECT(0x06)`, which clients are entitled to treat as permanent.

---

## 10. Configuration documents (`0x06` / `0x13`)

The phone's whole appearance and feel is a single JSON document. The client sends
its own with `CONFIG`; the PC sends a replacement with `SET_CONFIG`. Both use the
same schema, so a document captured from one phone can be pushed to another.

```json
{
  "v": 1,
  "type": "XBOX360",
  "name": "Ania",
  "screen": { "w": 2400, "h": 1080 },
  "layout": {
    "FACE":    { "x": 0.78, "y": 0.55, "s": 1.0, "r": 0 },
    "L_STICK": { "x": 0.20, "y": 0.62, "s": 1.1, "r": 0 }
  },
  "settings": {
    "haptics": true,
    "hapticStrength": 0.85,
    "gyro": false,
    "gyroSensitivity": 0.4,
    "touchVibration": true,
    "theme": "Dark"
  }
}
```

### Rules

* `v` is the schema version; a peer that does not recognise it ignores the whole
  document rather than guessing.
* `type` is the controller type name (`XBOX360`, `DUALSHOCK4`, `BUZZ`). Changing it
  in a `SET_CONFIG` makes the client reconnect so the handshake announces the new
  type.
* **`x` and `y` are fractions of the usable screen, `0.0`–`1.0`, and address the
  *centre* of the component.** They are deliberately *not* pixels: a layout authored
  on the PC has to land in the same place on any phone. Values outside the range are
  clamped by the receiver.
* `s` is a scale multiplier, clamped to `0.5`–`3.0`. `r` is a rotation in degrees,
  `-180`–`180`.
* `screen` is informational — the phone reports its pixel size so the PC preview can
  use the right aspect ratio. It is ignored in `SET_CONFIG`.
* Unknown keys are preserved where practical and never cause a rejection. Unknown
  component IDs in `layout` are dropped.
* A `SET_CONFIG` that omits `layout` changes only the settings, and vice versa —
  the receiver merges rather than replaces.

### Component IDs and nominal sizes

Nominal size is expressed as a fraction of screen **height** at `s = 1.0`. It is
what the PC preview draws and what the coordinate migration uses. Component
*centres* agree exactly between the phone and the PC; drawn sizes agree only
approximately, because the phone renders each widget at its intrinsic size. When
the two disagree, **the table follows the phone** — shrinking a widget to match a
number would make it unusable.

| Controller type | ID | Nominal size | Meaning |
| --- | --- | --- | --- |
| gamepad | `L_STICK`, `R_STICK` | 0.34 | analog sticks |
| gamepad | `DPAD` | 0.42 | d-pad cluster |
| gamepad | `FACE` | 0.42 | four face buttons |
| gamepad | `L1`, `R1` | 0.13 | shoulder buttons |
| gamepad | `L2`, `R2` | 0.15 | analog triggers |
| gamepad | `SHARE`, `OPTIONS` | 0.09 | Back/Start, Share/Options |
| gamepad | `PS` | 0.10 | Guide / PS button |
| Buzz | `BUZZ_RED` | 0.38 | the big buzzer |
| Buzz | `BUZZ_BLUE`, `BUZZ_ORANGE`, `BUZZ_GREEN`, `BUZZ_YELLOW` | 0.16 | answer buttons |

`XBOX360` and `DUALSHOCK4` share the gamepad ID set — only the glyphs differ, so
switching between them preserves the layout.

### Flow

```
phone                                   PC
  |  WELCOME received                    |
  |  CONFIG (current appearance) ------->|  dashboard renders a live preview
  |                                      |
  |  user edits the layout on the PC     |
  |<------------------------- SET_CONFIG |
  |  apply + persist                     |
  |  CONFIG (echo of what was applied) ->|  PC confirms it landed
```

The echo is what makes the PC's view authoritative: the dashboard only shows a
change as applied once the phone has confirmed it.

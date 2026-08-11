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
| `0x11` WELCOME | `slot:uint8, features:uint8` | handshake accepted; `slot` is 0-based. features bit0 = rumble available, bit1 = LED available |
| `0x1F` REJECT | `reason:uint8` | handshake refused, server closes immediately |
| `0x03` RUMBLE | `large:uint8, small:uint8` | force feedback, `0`…`255` |
| `0x12` LED | `r:uint8, g:uint8, b:uint8` | DS4 lightbar colour / Buzz lamp (non-zero = lamp on) |
| `0xF1` PONG | `seq:uint32` | echoes the `PING` sequence number |

Reject reasons:

| value | meaning |
| --- | --- |
| `0x01` | unsupported protocol version |
| `0x02` | invalid token |
| `0x03` | server full |
| `0x04` | malformed handshake |
| `0x05` | unauthenticated (non-HELLO sent first) |
| `0x06` | rate limited / too many failed attempts |

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

The token rotates on every server start unless the user pins it. Clients store the
token per server IP so reconnects are automatic.

---

## 9. Rate limiting

* Max **1000 INPUT messages/second** per connection; excess is dropped and counted.
* Max **5 failed handshakes per source IP per 60 s**, then `REJECT(0x06)` without
  even parsing the token.

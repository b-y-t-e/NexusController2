/* Nexus Controller dashboard.
 *
 * Deliberately dependency-free: the sparkline is ~30 lines of canvas drawing
 * rather than a charting library, so the page works with no network at all.
 */
'use strict';

const POLL_MS = 200;
const HISTORY = 60;

const BIND_BUTTONS = [
  ['a', 'A'], ['b', 'B'], ['x', 'X'], ['y', 'Y'], ['lb', 'LB'],
  ['rb', 'RB'], ['back', 'Back'], ['start', 'Start'], ['l3', 'L3'], ['r3', 'R3'],
  ['up', 'Up'], ['down', 'Down'], ['left', 'Left'], ['right', 'Right'], ['guide', 'Guide'],
];

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

let running = false;
let history = new Array(HISTORY).fill(0);
let slotCount = 0;   // real value arrives with the first state poll
let modalSlot = 0;
let pendingBind = null;
let lastLogLength = 0;
let ipsSignature = '';
let componentSets = {};
let lastPlayers = [];
/** Outcome of the last "Open port" click, kept until the banner goes away. */
let firewallMessage = null;

/* --- sparkline ---------------------------------------------------------- */

/* Backing-store size is derived from the *card*, never from the canvas itself.
 *
 * Reading canvas.clientWidth/Height to decide canvas.width/height is a loop
 * waiting for a reason to close: the width and height attributes are the
 * element's intrinsic size, so the moment CSS stops pinning its layout size —
 * one stray brace in the stylesheet did it — each redraw multiplies the last by
 * devicePixelRatio. At 100% scaling that is ×1 and nothing happens, which is why
 * it can sit there unseen; at 125% the canvas reached 10610×3156 in three
 * seconds, blew past what the compositor will allocate, and rendered as the
 * broken-image placeholder covering half the dashboard.
 *
 * The card's width cannot be pushed by the canvas (see min-width: 0 in the
 * stylesheet), and the height is a constant here, so neither can feed back.
 */
const CHART_HEIGHT = 84;          // keep in step with `.graph canvas` in style.css
const MAX_BACKING_PX = 8192;      // far above any real window, far below the limit

function drawChart() {
  const canvas = $('chart');
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.min(canvas.parentElement.clientWidth, MAX_BACKING_PX));
  const height = CHART_HEIGHT;
  // Pinned in CSS pixels as well, so the attributes below can never become the
  // thing that decides how tall the element is drawn.
  canvas.style.height = height + 'px';
  const backingW = Math.min(Math.round(width * ratio), MAX_BACKING_PX);
  const backingH = Math.min(Math.round(height * ratio), MAX_BACKING_PX);
  if (canvas.width !== backingW || canvas.height !== backingH) {
    canvas.width = backingW;
    canvas.height = backingH;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const accent = getComputedStyle(document.body).getPropertyValue('--accent').trim();
  const peak = Math.max(60, ...history);
  const stepX = width / (HISTORY - 1);
  const y = (value) => height - 3 - (value / peak) * (height - 8);

  ctx.strokeStyle = 'rgba(255,255,255,.05)';
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const gy = Math.round((height / 4) * i) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, gy);
    ctx.lineTo(width, gy);
    ctx.stroke();
  }

  ctx.beginPath();
  history.forEach((value, i) => {
    const px = i * stepX;
    if (i === 0) ctx.moveTo(px, y(value));
    else ctx.lineTo(px, y(value));
  });
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.lineTo(width, height);
  ctx.lineTo(0, height);
  ctx.closePath();
  ctx.fillStyle = accent + '22';
  ctx.fill();
}

/* --- players ------------------------------------------------------------ */

/* Packet counts run into the millions in an evening. Grouped with a thin space
   so the number stays a number rather than a wall of digits; toLocaleString is
   avoided because the separator would then follow the PC's locale and the rest
   of this dashboard is in one language. */
/* How long this phone has been connected, coarse on purpose: the useful reading
   is "just now" versus "all evening", never the exact second. */
function formatUptime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return total + ' s';
  if (total < 3600) return Math.floor(total / 60) + ' min';
  return Math.floor(total / 3600) + ' h ' + Math.floor((total % 3600) / 60) + ' min';
}

function formatCount(value) {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/* One bay in the rack.
 *
 * A bay is always in the DOM and always in slot order, because the slot number
 * is the thing the whole card exists to tell you: which player this is, and
 * which one a phone will get when it connects. An empty bay collapses to its
 * number and the word "free" — the parts that only mean something for a live
 * pad are hidden by CSS rather than rebuilt, so nothing shifts around when
 * somebody joins or leaves. */
function playerCard(index) {
  const node = document.createElement('div');
  node.className = 'bay';
  node.id = 'player-' + index;
  node.innerHTML =
    '<span class="bay-no">' + (index + 1) + '</span>' +
    '<div class="pad-mini bay-live" id="mini-' + index + '"></div>' +
    '<div class="bay-body">' +
      '<div class="bay-title">' +
        '<span class="bay-name" id="pname-' + index + '">Player ' + (index + 1) + '</span>' +
        '<span class="dtype bay-live" id="dtype-' + index + '"></span>' +
        '<span class="xtag bay-live hidden" id="xtag-' + index + '"></span>' +
      '</div>' +
      '<div class="bay-meta" id="pmeta-' + index + '"></div>' +
      '<div class="trigbars bay-live">' +
        '<div class="trigbar" data-label="LT"><span><i id="lt-' + index + '"></i></span></div>' +
        '<div class="trigbar" data-label="RT"><span><i id="rt-' + index + '"></i></span></div>' +
      '</div>' +
    '</div>' +
    '<div class="bay-uptime bay-live" id="uptime-' + index + '"></div>' +
    '<div class="bay-actions bay-live">' +
      '<button class="btn btn-ghost btn-sm" data-design="' + index + '">Layout</button>' +
      '<button class="btn btn-ghost btn-sm" data-config="' + index + '">Keys</button>' +
    '</div>';
  return node;
}

/* The slot count comes from the server, so the option list is built rather than
   written out in the HTML — otherwise raising MAX_PLAYERS silently leaves the
   extra slots unselectable here. Rebuilt only when the count actually changes,
   so an open dropdown is not torn out from under the user on every poll. */
function renderDesktopSlots(capacity) {
  if (capacity === slotCount) return;
  slotCount = capacity;
  const select = $('desktop-slot');
  select.innerHTML = '';
  for (let index = 0; index < capacity; index += 1) {
    const option = document.createElement('option');
    option.value = String(index);
    option.textContent = 'Player ' + (index + 1);
    select.appendChild(option);
  }
}

function renderPlayers(players) {
  const list = $('player-list');
  if (list.childElementCount !== players.length) {
    list.innerHTML = '';
    players.forEach((_, index) => list.appendChild(playerCard(index)));
    list.querySelectorAll('[data-config]').forEach((button) => {
      button.addEventListener('click', () => openModal(Number(button.dataset.config)));
    });
    list.querySelectorAll('[data-design]').forEach((button) => {
      button.addEventListener('click', () => openDesigner(Number(button.dataset.design)));
    });
  }

  players.forEach((player, index) => {
    const card = $('player-' + index);
    // Reserved but not connected: the phone is past the handshake and its pad is
    // being created. Saying "free" for that moment sends whoever is watching to
    // look for a phone that is in fact arriving.
    const joining = !player.connected && player.reserved;
    card.classList.toggle('on', player.connected);
    card.classList.toggle('joining', joining);

    $('pname-' + index).textContent = player.connected
      ? (player.name || 'Player ' + (index + 1))
      : (joining ? 'connecting…' : 'free');
    // Dropped frames mean the rate limiter is shedding input, which the player
    // feels as the pad missing presses — amber, not another grey number.
    $('pmeta-' + index).innerHTML = player.connected
      ? player.address + ' · ' + formatCount(player.packets) + ' pkt' +
        (player.dropped
          ? ' · <span class="bay-warn">' + formatCount(player.dropped) + ' dropped</span>'
          : '')
      : '';
    $('dtype-' + index).textContent = player.connected ? player.device_label : '';
    $('uptime-' + index).textContent = player.connected ? formatUptime(player.uptime) : '';

    // Our slot number and the game's player number are different things: XInput
    // has four places for the whole machine and hands them out in the order
    // devices appear, so slot 3 here can be player 1 in the game. A DS4 is HID
    // and gets no XInput number at all, which is why the badge can be absent.
    const xtag = $('xtag-' + index);
    const hasXInput = player.connected && player.xinput_index !== null &&
      player.xinput_index !== undefined;
    xtag.classList.toggle('hidden', !hasXInput);
    if (hasXInput) {
      const shown = player.xinput_index + 1;
      xtag.textContent = 'XInput ' + shown;
      xtag.title = 'Windows gave this pad XInput place ' + shown +
        ' — that is the number the game shows, and it need not match the slot.';
    }

    const visuals = player.visuals || {};
    $('lt-' + index).style.width = ((visuals.lt || 0) * 100) + '%';
    $('rt-' + index).style.width = ((visuals.rt || 0) * 100) + '%';

    // Live thumbnail of what this phone actually looks like right now.
    const mini = $('mini-' + index);
    if (player.connected && player.config) {
      const set = componentSets[player.config.type] || [];
      renderPad(mini, player.config, set, visuals, {});
    } else if (mini._padNodes || !mini.firstChild) {
      mini.innerHTML = '<div class="pad-mini-empty">' +
        (player.connected ? 'waiting for layout' : '') + '</div>';
      mini._padNodes = null;
      mini._padIds = null;
    }
  });
}

/* --- state loop --------------------------------------------------------- */

function applyState(state) {
  running = state.running;
  if (state.component_sets) componentSets = state.component_sets;
  renderDesktopSlots(state.capacity);
  document.body.classList.toggle('running', running);

  $('version').textContent = 'v' + state.version;
  $('sim-badge').classList.toggle('hidden', !state.simulated);
  $('driver-banner').classList.toggle('hidden', !state.simulated);
  $('status-text').textContent = running ? 'ONLINE' : 'OFF';
  $('status-sub').textContent = running
    ? state.ip + ':' + state.port
    : (state.error || 'not listening');

  const powerButton = $('power-btn');
  powerButton.textContent = running ? 'STOP SERVER' : 'START SERVER';
  powerButton.className = 'btn ' + (running ? 'btn-danger' : 'btn-primary');
  $('ip-select').disabled = running;

  const signature = state.ips.join(',') + '|' + (state.bind_ip || '');
  if (signature !== ipsSignature) {
    ipsSignature = signature;
    const select = $('ip-select');
    select.innerHTML = '<option value="AUTO">Auto-detect</option>'
      // For a PC on two networks at once — a docked laptop, Ethernet plus
      // Wi-Fi — where one bound address is reachable from one of them and
      // invisible from the other. The QR code still carries a real address.
      + '<option value="ALL">All interfaces</option>';
    if (state.bind_ip === '0.0.0.0') select.value = 'ALL';
    state.ips.forEach((ip) => {
      const option = document.createElement('option');
      option.value = ip;
      option.textContent = ip;
      if (ip === state.bind_ip) option.selected = true;
      select.appendChild(option);
    });
  }

  if (running && state.qr) {
    $('qr').src = 'data:image/png;base64,' + state.qr;
    $('qr').classList.remove('hidden');
    $('qr-placeholder').classList.add('hidden');
  } else {
    $('qr').classList.add('hidden');
    $('qr-placeholder').classList.remove('hidden');
  }
  // state.ip is what to dial, which under "All interfaces" is not what was
  // bound — 0.0.0.0 is an address no phone can connect to.
  $('pair-target').textContent = running
    ? state.ip + ':' + state.port + (state.all_interfaces ? ' (all interfaces)' : '')
    : '—';
  $('token').textContent = state.token || 'no token required';

  const banner = $('xinput-banner');
  banner.textContent = state.xinput_warning || '';
  banner.classList.toggle('hidden', !state.xinput_warning);

  $('slot-count').textContent = state.connected + ' / ' + state.capacity;
  $('pps').textContent = state.pps + ' pkt/s';
  history.push(state.pps);
  history.shift();
  drawChart();

  lastPlayers = state.players;
  renderPlayers(state.players);
  if (typeof designerOpen === 'function' && designerOpen()) drawDesigner();

  syncCheckbox($('haptics-toggle'), state.haptics);
  syncCheckbox($('desktop-toggle'), state.desktop_control);
  syncCheckbox($('pin-token'), state.pin_token);
  syncCheckbox($('require-token'), state.token_required);
  $('desktop-toggle').disabled = !state.desktop_available;
  $('desktop-warning').classList.toggle('hidden', !state.desktop_control);
  if (document.activeElement !== $('desktop-slot')) {
    $('desktop-slot').value = String(state.desktop_slot);
  }

  if (state.log.length !== lastLogLength) {
    lastLogLength = state.log.length;
    const box = $('log');
    box.innerHTML = '';
    state.log.forEach((line) => {
      const row = document.createElement('div');
      row.textContent = line;
      box.appendChild(row);
    });
    box.scrollTop = box.scrollHeight;
  }
}

function syncCheckbox(element, value) {
  if (document.activeElement !== element) element.checked = !!value;
}

function poll() {
  api().get_state().then(applyState).catch((error) => console.error(error));
}

/* --- key binding modal -------------------------------------------------- */

function openModal(slot) {
  modalSlot = slot;
  pendingBind = null;
  $('modal-title').textContent = 'Player ' + (slot + 1) + ' — key bindings';
  $('bind-status').innerHTML = '&nbsp;';
  $('modal').classList.remove('hidden');
  api().get_bindings(slot).then(renderBindings);
}

function renderBindings(bindings) {
  const grid = $('bind-grid');
  grid.innerHTML = '';
  BIND_BUTTONS.forEach(([id, label]) => {
    const button = document.createElement('button');
    button.className = 'bind' + (bindings[id] ? ' bound' : '');
    button.dataset.button = id;
    button.innerHTML = '<b>' + label + '</b><span>' + (bindings[id] || '—') + '</span>';
    button.addEventListener('click', () => startBinding(id, button));
    grid.appendChild(button);
  });
}

function startBinding(id, button) {
  document.querySelectorAll('.bind.listening').forEach((el) => el.classList.remove('listening'));
  button.classList.add('listening');
  pendingBind = id;
  $('bind-status').textContent = 'Press a key for ' + id.toUpperCase() + ' (Esc clears it)';
}

function onKeyDown(event) {
  if (pendingBind === null) return;
  if ($('modal').classList.contains('hidden')) return;
  event.preventDefault();
  const id = pendingBind;
  pendingBind = null;
  const key = event.key === 'Escape' ? '' : (event.key === ' ' ? 'space' : event.key.toLowerCase());
  api().set_key_bind(modalSlot, id, key).then((result) => {
    if (!result.ok) {
      $('bind-status').textContent = result.error;
      return;
    }
    renderBindings(result.bindings);
    $('bind-status').textContent = key
      ? id.toUpperCase() + ' → ' + key.toUpperCase()
      : id.toUpperCase() + ' cleared';
  });
}

function closeModal() {
  $('modal').classList.add('hidden');
  pendingBind = null;
}

/* --- wiring ------------------------------------------------------------- */

function wire() {
  $('power-btn').addEventListener('click', () => {
    const button = $('power-btn');
    button.disabled = true;
    $('start-error').classList.add('hidden');
    const action = running
      ? api().stop_server()
      : api().start_server($('ip-select').value);
    action
      .then((result) => {
        if (result && result.ok === false) {
          $('start-error').textContent = result.error;
          $('start-error').classList.remove('hidden');
        }
      })
      .finally(() => {
        button.disabled = false;
        poll();
        // Starting elevated adds the rules itself, so re-check rather than keep
        // showing a banner that is no longer true.
        refreshFirewall();
      });
  });

  $('haptics-toggle').addEventListener('change', (event) => {
    api().set_haptics(event.target.checked);
  });

  $('desktop-toggle').addEventListener('change', (event) => {
    api().set_desktop_control(event.target.checked).then((result) => {
      if (result && result.ok === false) {
        event.target.checked = false;
        alert(result.error);
      }
      poll();
    });
  });

  $('desktop-slot').addEventListener('change', (event) => {
    api().set_desktop_slot(Number(event.target.value));
  });

  $('pin-token').addEventListener('change', (e) => api().set_pin_token(e.target.checked));
  $('require-token').addEventListener('change', (e) => {
    api().set_require_token(e.target.checked).then(poll);
  });
  $('regen-token').addEventListener('click', () => api().regenerate_token().then(poll));

  $('modal-close').addEventListener('click', closeModal);
  $('modal-done').addEventListener('click', closeModal);
  $('clear-binds').addEventListener('click', () => {
    api().clear_bindings(modalSlot).then(() => api().get_bindings(modalSlot)).then(renderBindings);
  });
  $('test-rumble').addEventListener('click', () => {
    api().test_rumble(modalSlot, 0.7).then((sent) => {
      $('bind-status').textContent = sent ? 'Rumble sent' : 'Player not connected';
    });
  });

  document.querySelectorAll('[data-theme-name]').forEach((dot) => {
    dot.addEventListener('click', () => {
      const theme = dot.dataset.themeName;
      document.body.dataset.theme = theme;
      api().set_theme(theme);
      drawChart();
    });
  });

  wireDesigner();

  $('driver-install').addEventListener('click', () => {
    const button = $('driver-install');
    button.disabled = true;
    api().install_driver().then((result) => {
      $('driver-text').textContent = result.ok
        ? result.message + '. Reboot, then restart this app. '
        : ('Could not start the installer: ' + result.error + ' ');
      button.disabled = false;
    });
  });

  $('firewall-open').addEventListener('click', () => openFirewall(false));
  $('firewall-open-public').addEventListener('click', () => openFirewall(true));

  $('autostart-toggle').addEventListener('change', (event) => {
    autostartError = null;
    api().set_autostart(event.target.checked).then((status) => {
      autostartError = status.error || null;
      return applyAutostart(status);
    }).catch((error) => {
      console.warn('could not change the autostart entry:', error);
      autostartError = String(error);
      refreshAutostart();
    });
  });
  $('tray-toggle').addEventListener('change', (event) => {
    // Same shape as the autostart switch: a rejected call must not leave the box
    // showing a setting the app never took.
    api().set_close_to_tray(event.target.checked).then(refreshWindowState).catch((error) => {
      console.warn('could not change the close-to-tray setting:', error);
      refreshWindowState();
    });
  });

  $('updates-toggle').addEventListener('change', (event) => {
    api().set_check_updates(event.target.checked).then(refreshUpdate);
  });
  $('update-page').addEventListener('click', () => api().open_release_page());
  $('update-install').addEventListener('click', installUpdate);
  $('updates-check').addEventListener('click', () => {
    // A dismissed failure leaves nothing on screen; without this the way back to
    // an offer was to restart the app. The state machine refuses while something
    // is already running, so the button needs no guard of its own.
    updateMessage = null;
    // Disabled from the click, not from the next poll: until the bridge call
    // reaches Python the state still says whatever it said before, and a second
    // press in that window is a refusal shown to somebody who did nothing wrong.
    $('updates-check').disabled = true;
    // Said now and not kept: the poll replaces it with the answer, whatever it
    // turns out to be, and a sentence nobody has to dismiss is better than one
    // they do.
    showBanner('Checking GitHub for a newer version… ');
    api().check_for_update().then((status) => {
      if (status.started || status.state === 'checking') {
        // Ours, or one already running — whose answer is just as fresh and just
        // as much an answer to this button. Set only now: until this call comes
        // back the state is still the *previous* answer, and a poll landing in
        // that gap would report it as this check's own, "you have the latest
        // version" from a check that ran an hour ago.
        checkRequested = true;
      } else if (status.state === 'installing' || status.state === 'installed') {
        // Refused: something else owns the state and no check ran.
        showUpdateMessage(checkRefusal(status));
      } else {
        // It started and finished inside this call, or one that was already
        // running did — `started` says which, and either way this is the answer.
        reportCheckResult(status);
      }
      return refreshUpdate();
    }).catch((error) => {
      checkRequested = false;
      $('updates-check').disabled = false;
      showUpdateMessage('Could not check for updates: ' + error + ' ');
    });
  });
  // The one thing left to do in the "installed" state, which nothing else can
  // leave: this build is finished, and the new one is waiting on disk.
  $('update-close').addEventListener('click', () => api().quit());
  $('update-dismiss').addEventListener('click', () => {
    // Both copies of the same sentence: the sticky one on the page and the one
    // the state carries so the offer can explain itself. Clearing only the first
    // would bring the second back on the next poll, three seconds later.
    updateMessage = null;
    api().dismiss_update_error().then(refreshUpdate).catch((error) => {
      console.warn('could not clear the update error:', error);
      refreshUpdate();
    });
  });

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', drawChart);
}

function openFirewall(includePublic) {
  const button = includePublic ? $('firewall-open-public') : $('firewall-open');
  button.disabled = true;
  $('firewall-text').textContent = 'Waiting for Administrator approval… ';
  api().open_firewall(includePublic).then((result) => {
    // The server has already waited for the rules to appear, so this is an
    // outcome, not a "started". It stays on screen: an earlier version let the
    // next refresh overwrite it, which made a failed attempt look like a
    // button that did nothing at all.
    firewallMessage = result.message + ' ';
  }).catch((error) => {
    // A rejected bridge call is still an answer. Without this the button
    // stayed disabled for good — the one outcome worse than a bad message.
    firewallMessage = 'Could not ask Windows to open the port: ' + error + ' ';
  }).then(() => {
    $('firewall-text').textContent = firewallMessage;
    button.disabled = false;
    refreshFirewall();
  });
}

/* Checked on demand rather than in the poll loop: answering costs two netsh
   calls, and the answer only changes when somebody changes it. */
function refreshFirewall() {
  api().firewall_status().then((info) => {
    const banner = $('firewall-banner');
    // `open` is null when we cannot tell (not Windows, or netsh unavailable);
    // claiming the port is blocked on no evidence would be worse than silence.
    banner.classList.toggle('hidden', info.open !== false);
    if (info.open !== false) {
      firewallMessage = null;
      return;
    }
    // A rule for the private profile does nothing on a network Windows calls
    // Public — which is what it calls a phone hotspot unless told otherwise.
    // Say so, because otherwise every rule is in place and nothing works.
    const onPublic = info.public_networks && info.public_networks.length;
    $('firewall-open-public').classList.toggle('hidden', !onPublic);

    // Never clobber the result of a click the user is still reading.
    $('firewall-text').textContent = firewallMessage ||
      ('Windows Firewall is blocking TCP ' + info.tcp + ' / UDP ' + info.udp +
       ', so phones cannot reach this PC over ' + info.needed_for +
       ' (USB works either way). ' +
       (onPublic
         ? 'Windows lists "' + info.public_networks.join('", "') +
           '" as a public network, and the rule only covers private ones. ' +
           'Set that network to private in Windows settings, or open the port ' +
           'for public networks too — which also applies on every other public ' +
           'network you join. '
         : '') +
       (info.others && info.others.length
         ? info.others.join(', ') + ' is also installed and filters separately. '
         : ''));
    $('firewall-open').textContent = info.admin ? 'Open port' : 'Open port (asks for admin)';
  }).catch((error) => {
    // The banner is a diagnostic; it must not become the thing that needs
    // diagnosing. A failed bridge call just leaves it as it was.
    console.warn('firewall status unavailable:', error);
  });
}

// The check runs in the background on the Python side, so the page asks for the
// answer a few times rather than waiting for one. Slow and quiet on purpose:
// there is nothing here worth interrupting anybody for.
const UPDATE_POLL_MS = 3000;

// The login entry and the tray, re-read from the machine. Rarer still: one of
// them opens the registry, and neither changes unless somebody goes looking for
// it somewhere else in Windows.
const SETTINGS_POLL_MS = 30000;

/** Outcome of the last "Download and install" click, kept until it is acted on.
 *
 * The poll below runs every three seconds and decides what the banner says from
 * the state alone — so the sentence explaining why an install failed was on
 * screen for at most three seconds before the banner hid itself, which is not
 * long enough to read a sentence, let alone act on one. The firewall banner
 * already had this exact problem and this is the same answer. */
let updateMessage = null;

/** Set while a check the user asked for by hand is still running.
 *
 * The automatic check at start-up says nothing when there is nothing to say —
 * an app for playing games in the same room as the PC has no business
 * interrupting anybody about a point release. A check somebody *pressed a button
 * for* is the opposite: two of its three outcomes ("nothing newer", "could not
 * reach GitHub") leave the banner hidden, so without this the button looked
 * broken in exactly the cases where the user is waiting for an answer. The phone
 * has always said "This is the latest version." */
let checkRequested = false;

/** Set from the click until install_update() answers.
 *
 * The button disables itself on click, but the poll below re-enables it from the
 * state — and the state does not say "installing" until the bridge call has
 * crossed to Python and begun. A poll landing in that window put a live button
 * back under a download that was already starting. The server refuses the second
 * install (UpdateState.begin_install is a compare-and-set), so nothing breaks,
 * but a button that answers "an update is already being installed" is the same
 * check-then-act the Python side stopped doing. */
let installing = false;

function refreshUpdate() {
  return api().update_status().then((status) => {
    syncCheckbox($('updates-toggle'), status.enabled);
    if (checkRequested && status.state !== 'checking') {
      checkRequested = false;
      reportCheckResult(status);
    }
    // Nothing new can start while a check or an install owns the state, and the
    // button that says so is better than the refusal that comes back from
    // pressing it. Same rule as the install button two blocks down.
    $('updates-check').disabled = status.state === 'checking'
      || status.state === 'installing' || status.state === 'installed';
    const banner = $('update-banner');
    const available = status.state === 'available' && status.latest;
    const waiting = checkRequested && status.state === 'checking';
    // "installed" is the one state with something standing to say: the swap has
    // happened and this window is still the old build, so the restart is the
    // only thing left to do and the banner must not be dismissable away from it.
    // A check somebody pressed a button for speaks too, for as long as it runs —
    // otherwise the banner it just put up disappears at the next poll and the
    // button gives nothing back for up to ten seconds of network timeout.
    const speaks = available || waiting
      || status.state === 'installing' || status.state === 'installed';
    banner.classList.toggle('hidden', !speaks && !updateMessage);

    // Whatever the state says, a message the user has not dealt with yet wins.
    // "Dealt with" has to mean something they can actually do, or the banner is
    // simply stuck: the sentence stays until Dismiss puts the banner back under
    // the control of the state, which is what every other banner here does.
    // Either copy of the sentence keeps the way out visible. The page's own copy
    // does not survive a reload of the window; the state's does, and without this
    // it would then be a message with no button to clear it.
    $('update-dismiss').classList.toggle('hidden', !updateMessage && !status.error);
    // Decided from the state, not from the branch below, so it is there under a
    // sticky message too — that is exactly when the user is being told to close.
    $('update-close').classList.toggle('hidden', status.state !== 'installed');
    // The same reason, and it matters more here: a sticky message returns early,
    // so a button left over from an earlier state stayed on the banner offering
    // to install a version that is already on disk. Nothing to install from a
    // source checkout or a directory this process may not write to either; the
    // page then offers the releases page instead.
    const canInstall = available && status.can_install && status.has_asset;
    $('update-install').classList.toggle('hidden', !canInstall);
    $('update-install').disabled = installing || status.state === 'installing';

    if (updateMessage) {
      $('update-text').textContent = updateMessage;
      return status;
    }

    if (waiting) {
      $('update-text').textContent = 'Checking GitHub for a newer version… ';
      return status;
    }
    if (status.state === 'installing') {
      $('update-text').textContent = 'Downloading the new version… ';
      return status;
    }
    if (status.state === 'installed') {
      // status.error is why the new build did not start itself, and it outlives
      // the page's own copy of that sentence — same asymmetry the offer above
      // already accounts for.
      $('update-text').textContent =
        'The update is installed. Close this window and start Nexus Controller again. ' +
        (status.error ? '(' + status.error + ') ' : '');
      return status;
    }
    if (available) {
      // status.error survives a failed install: the state goes back to the offer
      // it was before, because the release is still there and only the attempt
      // failed. Saying so is the difference between an honest second chance and
      // a button that already let the user down once without explanation.
      $('update-text').textContent =
        'Version ' + status.latest + ' is available — you have ' + status.current + '. ' +
        (status.error ? 'The last attempt failed: ' + status.error + ' ' : '');
    }
    return status;
  }).catch((error) => {
    console.warn('update status unavailable:', error);
  });
}

/* Starting with Windows, and what the X button does.
 *
 * Both are read back from the machine rather than from anything this page
 * remembers: the login entry lives in the registry, where Task Manager's
 * Start-up tab can switch it off, and the tray may simply have failed to start.
 * A switch that shows what it was told rather than what is true is worse than no
 * switch at all. */
/** The last refusal from set_autostart, kept until the user tries again.
 *
 * The refresh below runs every three seconds and carries no error of its own, so
 * without somewhere to keep it the reason would be replaced before it could be
 * read. It goes when the next toggle answers — whatever that answer is. */
let autostartError = null;

function applyAutostart(status) {
  syncCheckbox($('autostart-toggle'), status.enabled);
  $('autostart-toggle').disabled = !status.supported;
  // A locked-down machine can refuse even HKCU. The switch then springs back to
  // what the registry says, which on its own looks like the click was missed —
  // so the reason is said here, next to it.
  const failure = status.error || autostartError;
  $('autostart-label').textContent = failure
    ? 'Start with Windows — could not change it: ' + failure
    : (status.supported ? 'Start with Windows' : 'Start with Windows — ' + status.reason);
  $('autostart-label').classList.toggle('warn', !!failure);
  return status;
}

function refreshAutostart() {
  return api().autostart_status().then(applyAutostart).catch((error) => {
    console.warn('autostart state unavailable:', error);
  });
}

function refreshWindowState() {
  return api().window_state().then((state) => {
    syncCheckbox($('tray-toggle'), state.close_to_tray && state.tray_running);
    $('tray-toggle').disabled = !state.tray_running;
    $('tray-label').textContent = state.tray_running
      ? 'Closing the window keeps it running in the tray'
      : 'Closing the window quits — there is no tray icon on this machine';
    return state;
  }).catch((error) => {
    console.warn('window state unavailable:', error);
  });
}

/** Why a check the user asked for did not start.
 *
 * The state machine refuses in exactly two situations and both are about
 * something better already happening. Nothing else belongs here: a state this
 * does not name means the check *did* run, and its answer is the thing to show.
 */
function checkRefusal(status) {
  if (status.state === 'installing') return 'An update is being installed right now. ';
  return 'The update is installed. Close this window and start Nexus Controller again. ';
}

/** Report the answer of a check the user asked for. Silent when it is an offer:
 *  the banner below says it better than a sentence would. */
function reportCheckResult(status) {
  if (status.state === 'none') {
    showUpdateMessage('You have the latest version (' + status.current + '). ');
  } else if (status.state === 'error') {
    showUpdateMessage('Could not check for updates: ' + status.error + ' ');
  }
}

function showBanner(text) {
  $('update-text').textContent = text;
  $('update-banner').classList.remove('hidden');
}

/** Say something in the banner and keep it there, with the way out beside it.
 *
 * Written here and not left to the next poll: three seconds is a long time to
 * look at a sentence with no visible way to make it go away. */
function showUpdateMessage(text) {
  updateMessage = text;
  showBanner(text);
  $('update-dismiss').classList.remove('hidden');
}

function installUpdate() {
  const button = $('update-install');
  if (installing) return;
  installing = true;
  button.disabled = true;
  updateMessage = null;
  $('update-dismiss').classList.add('hidden');
  $('update-text').textContent = 'Downloading the new version… ';
  api().install_update().then((result) => {
    // Whatever the answer, the Python state now speaks for itself: "installing"
    // while it runs, "installed" when it is done, the offer again when it failed.
    installing = false;
    if (result.ok) {
      if (!result.restarting) {
        // Installed, but the new build would not start. Closing this window now
        // would leave the user with nothing running and no explanation — so the
        // message stays and the one useful action comes with it, here rather
        // than up to three seconds later when the poll next runs.
        showUpdateMessage('Version ' + result.version + ' is installed. ' + result.error + ' ');
        $('update-close').classList.remove('hidden');
        $('update-install').classList.add('hidden');
        return;
      }
      showUpdateMessage(
        'Version ' + result.version + ' is installed and starting. This window will close. '
      );
      // The new build is already running and wants the port this one holds.
      setTimeout(() => api().quit(), 1200);
      return;
    }
    showUpdateMessage('Update failed: ' + result.error + ' ');
    button.disabled = false;
  }).catch((error) => {
    // A rejected bridge call is still an answer, and the one case where saying
    // nothing is worst: without this the button stays disabled and the banner
    // reads "Downloading the new version…" until the app is restarted.
    installing = false;
    showUpdateMessage('The update could not be started: ' + error + ' ');
    button.disabled = false;
  });
}

window.addEventListener('pywebviewready', () => {
  wire();
  api().get_state().then((state) => {
    document.body.dataset.theme = state.theme || 'cyan';
    applyState(state);
  });
  refreshFirewall();
  refreshUpdate();
  refreshAutostart();
  refreshWindowState();
  setInterval(poll, POLL_MS);
  setInterval(refreshUpdate, UPDATE_POLL_MS);
  // Slowest of the three, because these two are the least likely to change and
  // the autostart one opens the registry to answer. They are polled at all
  // because they *can* change behind this page — Task Manager's Start-up tab
  // switches the login entry off, and a tray icon that dies takes the meaning of
  // the X button with it — and a switch showing what it was told rather than
  // what is true is worse than no switch. Half a minute is soon enough for a
  // window somebody left open; every action on this page answers immediately.
  setInterval(() => {
    refreshAutostart();
    refreshWindowState();
  }, SETTINGS_POLL_MS);
});

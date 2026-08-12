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

function drawChart() {
  const canvas = $('chart');
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight || 90;
  if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
    canvas.width = width * ratio;
    canvas.height = height * ratio;
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

  const signature = state.ips.join(',');
  if (signature !== ipsSignature) {
    ipsSignature = signature;
    const select = $('ip-select');
    select.innerHTML = '<option value="AUTO">Auto-detect</option>';
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
  $('pair-target').textContent = running ? state.ip + ':' + state.port : '—';
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

window.addEventListener('pywebviewready', () => {
  wire();
  api().get_state().then((state) => {
    document.body.dataset.theme = state.theme || 'cyan';
    applyState(state);
  });
  refreshFirewall();
  setInterval(poll, POLL_MS);
});

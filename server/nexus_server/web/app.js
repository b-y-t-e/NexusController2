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
let slotCount = 4;
let modalSlot = 0;
let pendingBind = null;
let lastLogLength = 0;
let ipsSignature = '';
let componentSets = {};
let lastPlayers = [];

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

function playerCard(index) {
  const node = document.createElement('div');
  node.className = 'player';
  node.id = 'player-' + index;
  node.innerHTML =
    '<div class="pad-mini" id="mini-' + index + '"></div>' +
    '<div>' +
      '<div class="player-name" id="pname-' + index + '">Player ' + (index + 1) + '</div>' +
      '<div class="player-meta" id="pmeta-' + index + '">offline</div>' +
      '<div class="trigbars">' +
        '<div class="trigbar"><i id="lt-' + index + '"></i></div>' +
        '<div class="trigbar"><i id="rt-' + index + '"></i></div>' +
      '</div>' +
    '</div>' +
    '<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">' +
      '<span class="dtype" id="dtype-' + index + '">—</span>' +
      '<button class="btn btn-ghost btn-sm" data-design="' + index + '">Layout</button>' +
      '<button class="btn btn-ghost btn-sm" data-config="' + index + '">Keys</button>' +
    '</div>';
  return node;
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
    card.classList.toggle('on', player.connected);
    $('pname-' + index).textContent = player.connected
      ? (player.name || 'Player ' + (index + 1))
      : 'Player ' + (index + 1);
    $('pmeta-' + index).textContent = player.connected
      ? player.address + ' · ' + player.packets + ' pkt' +
        (player.dropped ? ' · ' + player.dropped + ' dropped' : '')
      : 'offline';
    $('dtype-' + index).textContent = player.connected ? player.device_label : '—';

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
        (player.connected ? 'waiting for layout' : 'offline') + '</div>';
      mini._padNodes = null;
      mini._padIds = null;
    }
  });
}

/* --- state loop --------------------------------------------------------- */

function applyState(state) {
  running = state.running;
  if (state.component_sets) componentSets = state.component_sets;
  slotCount = state.capacity;
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

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', drawChart);
}

window.addEventListener('pywebviewready', () => {
  wire();
  api().get_state().then((state) => {
    document.body.dataset.theme = state.theme || 'cyan';
    applyState(state);
  });
  setInterval(poll, POLL_MS);
});

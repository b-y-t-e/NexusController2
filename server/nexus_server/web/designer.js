/* The pad designer.
 *
 * This is the point of the whole feature: arrange a phone's controls here, on the
 * PC, and push the result to one device or to every connected device at once —
 * instead of repeating the same fiddling on each phone.
 *
 * Depends on globals from app.js (`api`, `$`, `componentSets`, `lastPlayers`,
 * `running`) and on `renderPad` from pad.js.
 */
'use strict';

let designSlot = 0;
let designConfig = null;    // the document being edited
let designSelected = null;  // id of the currently selected component
let designPushTimer = null;

function designerOpen() {
  return !$('designer').classList.contains('hidden');
}

function openDesigner(slot) {
  designSlot = slot;
  designSelected = null;
  setDesignStatus('');
  $('selected-name').textContent = 'nothing selected';
  $('scale-range').disabled = true;
  $('rot-range').disabled = true;
  $('designer').classList.remove('hidden');

  api().get_pad_config(slot).then((info) => {
    designConfig = info.config;
    componentSets[designConfig.type] = info.components;
    $('designer-title').textContent = 'Pad designer — Player ' + (slot + 1);
    $('designer-sub').textContent = info.connected
      ? (info.reported
        ? 'Drag the controls to arrange the pad. Changes go to the phone as you make them.'
        : 'Phone is connected but has not reported a layout yet — showing the default.')
      : 'Player not connected. You can still design a layout and save it as a profile.';
    $('phone-frame').style.setProperty('--aspect', info.aspect || 2.22);
    refreshProfiles(info.profiles);
    drawDesigner();
  });
}

function closeDesigner() {
  $('designer').classList.add('hidden');
  designConfig = null;
  designSelected = null;
  const canvas = $('pad-canvas');
  canvas._padNodes = null;
  canvas._padIds = null;
  canvas.innerHTML = '';
}

function designComponents() {
  return (designConfig && componentSets[designConfig.type]) || [];
}

function drawDesigner() {
  if (!designConfig) return;

  renderPad($('pad-canvas'), designConfig, designComponents(), liveVisuals(designSlot), {
    interactive: true,
    selected: designSelected,
    onSelect: selectComponent,
    onMove: (id, x, y) => {
      const place = placementFor(id);
      place.x = x;
      place.y = y;
      drawDesigner();
    },
    onCommit: schedulePush,
  });

  document.querySelectorAll('#type-switch button').forEach((button) => {
    button.classList.toggle('on', button.dataset.type === designConfig.type);
  });
}

function placementFor(id) {
  if (!designConfig.layout[id]) {
    designConfig.layout[id] = { x: 0.5, y: 0.5, s: 1, r: 0 };
  }
  return designConfig.layout[id];
}

/** Live button presses, so you can confirm a control is where your thumb lands. */
function liveVisuals(slot) {
  const player = lastPlayers && lastPlayers[slot];
  return player && player.connected ? player.visuals : null;
}

function selectComponent(id) {
  designSelected = id;
  const place = placementFor(id);
  const component = designComponents().find((c) => c.id === id);
  $('selected-name').textContent = component ? component.label : id;

  const scale = $('scale-range');
  const rotation = $('rot-range');
  scale.disabled = false;
  rotation.disabled = false;
  scale.value = place.s == null ? 1 : place.s;
  rotation.value = place.r || 0;
  $('scale-value').textContent = Number(scale.value).toFixed(2);
  $('rot-value').textContent = Math.round(Number(rotation.value)) + '°';
  drawDesigner();
}

function updateSelected(field, value) {
  if (!designSelected || !designConfig) return;
  placementFor(designSelected)[field] = value;
  drawDesigner();
  schedulePush();
}

/* Live editing should feel immediate, but a drag must not send one packet per
   pixel — pushes are coalesced into a single update. */
function schedulePush() {
  if (!$('live-push').checked) return;
  clearTimeout(designPushTimer);
  designPushTimer = setTimeout(() => pushDesign(false), 220);
}

function pushDesign(explicit) {
  if (!designConfig) return;
  api().push_pad_config(designSlot, designConfig).then((result) => {
    if (result.ok) {
      setDesignStatus(explicit ? 'Sent to phone' : 'Applied');
    } else if (explicit) {
      setDesignStatus(result.error);
    }
  });
}

function setDesignStatus(text) {
  $('designer-status').textContent = text || ' ';
}

function refreshProfiles(names) {
  const list = $('profile-list');
  const previous = list.value;
  list.innerHTML = '';
  (names || []).forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    list.appendChild(option);
  });
  if (previous) list.value = previous;
}

function switchType(type) {
  if (!designConfig) return;
  api().set_pad_device_type(designSlot, type).then((result) => {
    if (result.ok) {
      designConfig = result.config;
      setDesignStatus('Controller type changed — the phone reconnects to announce it');
    } else {
      // Not connected: still let the user design for that type offline.
      designConfig.type = type;
      setDesignStatus(result.error);
    }
    designSelected = null;
    api().get_components(designConfig.type).then((set) => {
      componentSets[designConfig.type] = set;
      drawDesigner();
    });
  });
}

function wireDesigner() {
  $('designer-close').addEventListener('click', closeDesigner);

  document.querySelectorAll('#type-switch button').forEach((button) => {
    button.addEventListener('click', () => switchType(button.dataset.type));
  });

  $('scale-range').addEventListener('input', (event) => {
    $('scale-value').textContent = Number(event.target.value).toFixed(2);
    updateSelected('s', Number(event.target.value));
  });
  $('rot-range').addEventListener('input', (event) => {
    $('rot-value').textContent = Math.round(Number(event.target.value)) + '°';
    updateSelected('r', Number(event.target.value));
  });

  $('push-one').addEventListener('click', () => pushDesign(true));

  $('push-all').addEventListener('click', () => {
    if (!designConfig) return;
    api().push_pad_config_to_all(designConfig).then((result) => {
      setDesignStatus(result.ok ? 'Sent to ' + result.sent + ' phone(s)' : result.error);
    });
  });

  $('reset-layout').addEventListener('click', () => {
    api().reset_pad_layout(designSlot).then((result) => {
      if (!result.ok) { setDesignStatus(result.error); return; }
      designConfig = result.config;
      designSelected = null;
      drawDesigner();
      setDesignStatus('Reset to default');
    });
  });

  $('profile-save').addEventListener('click', () => {
    const name = $('profile-name').value.trim();
    api().save_profile(name, designConfig).then((result) => {
      if (!result.ok) { setDesignStatus(result.error); return; }
      refreshProfiles(result.profiles);
      $('profile-name').value = '';
      setDesignStatus('Saved as "' + name + '"');
    });
  });

  $('profile-load').addEventListener('click', () => {
    const name = $('profile-list').value;
    if (!name) return;
    api().load_profile(name).then((result) => {
      if (!result.ok) { setDesignStatus(result.error); return; }
      designConfig = result.config;
      designSelected = null;
      api().get_components(designConfig.type).then((set) => {
        componentSets[designConfig.type] = set;
        drawDesigner();
        setDesignStatus('Loaded "' + name + '" — not sent yet');
      });
    });
  });

  $('profile-delete').addEventListener('click', () => {
    const name = $('profile-list').value;
    if (!name) return;
    api().delete_profile(name).then((result) => refreshProfiles(result.profiles));
  });

  $('designer-hint').textContent =
    'Tip: save a layout as a profile, then use "Push to all connected" to put it on every phone at once.';
}

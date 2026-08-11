/* Rendering a pad layout.
 *
 * One implementation drives both the small live preview on each player card and
 * the large editable canvas in the designer, so what you drag is exactly what you
 * see on the card — and both match the phone, because positions are fractions of
 * the screen rather than pixels.
 */
'use strict';

/* Which protocol bits light a component up. `low`/`high` are masks into
 * buttons_low / buttons_high; `axis` names a stick to deflect. */
const PAD_BITS = {
  FACE:      { low: 0x0F },
  L1:        { low: 0x10 },
  R1:        { low: 0x20 },
  SHARE:     { low: 0x40 },
  OPTIONS:   { low: 0x80 },
  DPAD:      { high: 0x3C },
  PS:        { high: 0x40 },
  L_STICK:   { high: 0x01, axis: 'l' },
  R_STICK:   { high: 0x02, axis: 'r' },
  L2:        { trigger: 'lt' },
  R2:        { trigger: 'rt' },
  BUZZ_RED:    { low: 0x01, color: '#ef4444' },
  BUZZ_YELLOW: { low: 0x02, color: '#eab308' },
  BUZZ_GREEN:  { low: 0x04, color: '#22c55e' },
  BUZZ_ORANGE: { low: 0x08, color: '#f97316' },
  BUZZ_BLUE:   { low: 0x10, color: '#3b82f6' },
};

/* Glyphs differ per controller type; the wire bits never do. */
const PAD_LABELS = {
  XBOX360:    { L1: 'LB', R1: 'RB', L2: 'LT', R2: 'RT', SHARE: 'Back', OPTIONS: 'Start', PS: 'Guide', FACE: 'ABXY' },
  DUALSHOCK4: { L1: 'L1', R1: 'R1', L2: 'L2', R2: 'R2', SHARE: 'Share', OPTIONS: 'Options', PS: 'PS', FACE: '✕○□△' },
  BUZZ:       { BUZZ_RED: 'BUZZ', BUZZ_BLUE: 'Blue', BUZZ_ORANGE: 'Orange', BUZZ_GREEN: 'Green', BUZZ_YELLOW: 'Yellow' },
};

function padLabel(type, id, fallback) {
  const table = PAD_LABELS[type] || {};
  return table[id] || fallback || id;
}

/**
 * Draw a layout into `host`.
 *
 * @param host       element to fill; it is sized to `aspect` by the caller
 * @param config     configuration document ({type, layout})
 * @param components component metadata from the server ([{id,label,size,shape}])
 * @param visuals    live input state, or null for a static preview
 * @param options    {interactive, selected, onSelect, onMove}
 */
function renderPad(host, config, components, visuals, options) {
  const opts = options || {};
  const layout = (config && config.layout) || {};
  const type = (config && config.type) || 'XBOX360';
  const height = host.clientHeight || 1;

  // Reuse nodes across frames so a drag is not interrupted by a repaint.
  let nodes = host._padNodes;
  const wantedIds = components.map((c) => c.id).join(',');
  if (!nodes || host._padIds !== wantedIds) {
    host.innerHTML = '';
    nodes = {};
    components.forEach((component) => {
      const node = document.createElement('div');
      node.className = 'pad-part' + (component.shape === 'pad' ? ' square' : '');
      node.dataset.id = component.id;
      node.innerHTML = '<span></span>';
      if (opts.interactive) attachDrag(node, component, host, opts);
      host.appendChild(node);
      nodes[component.id] = node;
    });
    host._padNodes = nodes;
    host._padIds = wantedIds;
  }

  components.forEach((component) => {
    const node = nodes[component.id];
    const place = layout[component.id] || { x: 0.5, y: 0.5, s: 1, r: 0 };
    const size = component.size * (place.s || 1) * height;

    node.style.width = size + 'px';
    node.style.height = size + 'px';
    node.style.left = (place.x * 100) + '%';
    node.style.top = (place.y * 100) + '%';
    node.style.transform = 'translate(-50%, -50%) rotate(' + (place.r || 0) + 'deg)';
    node.style.fontSize = Math.max(7, Math.min(13, size * 0.28)) + 'px';

    const bits = PAD_BITS[component.id] || {};
    node.style.setProperty('--part-color', bits.color || 'var(--accent)');
    node.querySelector('span').textContent =
      size > 22 ? padLabel(type, component.id, component.label) : '';

    node.classList.toggle('selected', opts.selected === component.id);
    node.classList.toggle('active', isActive(bits, visuals));

    if (bits.trigger && visuals) {
      node.style.setProperty('--fill', ((visuals[bits.trigger] || 0) * 100) + '%');
      node.classList.add('has-fill');
    }
    if (bits.axis && visuals) {
      const dx = visuals[bits.axis + 'x'] || 0;
      const dy = visuals[bits.axis + 'y'] || 0;
      node.querySelector('span').style.transform =
        'translate(' + (dx * size * 0.22) + 'px,' + (-dy * size * 0.22) + 'px)';
    }
  });
}

function isActive(bits, visuals) {
  if (!visuals) return false;
  if (bits.low && (visuals.buttons_low & bits.low)) return true;
  if (bits.high && (visuals.buttons_high & bits.high)) return true;
  if (bits.trigger && (visuals[bits.trigger] || 0) > 0.08) return true;
  return false;
}

/* Dragging updates the layout in fractions, so it stays screen-independent. */
function attachDrag(node, component, host, opts) {
  node.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    if (opts.onSelect) opts.onSelect(component.id);
    node.setPointerCapture(event.pointerId);

    const box = host.getBoundingClientRect();
    const start = node.getBoundingClientRect();
    // Grab offset, so the component does not jump to the cursor.
    const grabX = event.clientX - (start.left + start.width / 2);
    const grabY = event.clientY - (start.top + start.height / 2);

    const move = (moveEvent) => {
      const x = (moveEvent.clientX - grabX - box.left) / box.width;
      const y = (moveEvent.clientY - grabY - box.top) / box.height;
      if (opts.onMove) {
        opts.onMove(component.id, clamp01(x), clamp01(y));
      }
    };
    const up = () => {
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', up);
      node.removeEventListener('pointercancel', up);
      if (opts.onCommit) opts.onCommit();
    };
    node.addEventListener('pointermove', move);
    node.addEventListener('pointerup', up);
    node.addEventListener('pointercancel', up);
  });
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

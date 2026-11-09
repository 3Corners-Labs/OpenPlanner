// ADR 0117 point 19 (step 12), ADR 0118: interactive usage charts as an enhancement of the
// server-rendered chart. <op-report-chart> wraps a figure (an SVG with data-i and data-s on every
// point, then the same numbers as a table). Everything the script shows is taken out of that table
// or out of data-* words the server wrote in the page's language, and is set with textContent; no
// data comes in any other way. Without the script the chart, the table and the day links work as
// they are. Adds: a tip on hover and on keyboard focus (arrow keys), series toggles (buttons with
// aria-pressed), download as SVG or PNG, and Enter on a day follows that day's ordinary link.

const SVG_NS = 'http://www.w3.org/2000/svg';
const PATTERNED = /rchart__s([2-4])/;
let counter = 0;

function text(node) {
  return (node.textContent || '').trim().replace(/\s+/g, ' ');
}

function button(label, onClick, pressed) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'button button--secondary rchart__button';
  b.textContent = label;
  if (pressed !== undefined) b.setAttribute('aria-pressed', String(pressed));
  b.addEventListener('click', onClick);
  return b;
}

function save(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A copy of the drawing that carries its own colours, so the file looks right outside the page.
// The colour the chart sits on: the first opaque background on the way up, else white.
function surface(el) {
  for (let n = el; n; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
  }
  return '#fff';
}

function standalone(svg) {
  const clone = svg.cloneNode(true);
  const from = svg.querySelectorAll('*');
  const to = clone.querySelectorAll('*');
  const props = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'font-size', 'font-family', 'font-weight', 'opacity'];
  from.forEach((el, i) => {
    const css = getComputedStyle(el);
    props.forEach((p) => {
      const v = css.getPropertyValue(p);
      if (v) to[i].style.setProperty(p, v);
    });
    if (el.classList.contains('rchart__off')) to[i].remove();
  });
  const box = svg.viewBox.baseVal;
  const back = document.createElementNS(SVG_NS, 'rect');
  back.setAttribute('width', String(box.width));
  back.setAttribute('height', String(box.height));
  back.style.setProperty('fill', surface(svg));
  clone.insertBefore(back, clone.firstChild);
  clone.setAttribute('xmlns', SVG_NS);
  clone.setAttribute('width', String(box.width));
  clone.setAttribute('height', String(box.height));
  return clone;
}

function hatch(ctx, x, y, w, h, kind, color) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  if (kind === '4') {
    for (let d = -h; d < w + h; d += 6) {
      for (const s of [1, -1]) {
        ctx.beginPath();
        ctx.moveTo(x + d, s === 1 ? y : y + h);
        ctx.lineTo(x + d + h, s === 1 ? y + h : y);
        ctx.stroke();
      }
    }
  } else if (kind === '3') {
    for (let px = x + 3; px < x + w; px += 6) {
      for (let py = y + 3; py < y + h; py += 6) {
        ctx.beginPath();
        ctx.arc(px, py, 1.5, 0, 2 * Math.PI);
        ctx.fill();
      }
    }
  } else {
    for (let d = -h; d < w; d += 6) {
      ctx.beginPath();
      ctx.moveTo(x + d, y + h);
      ctx.lineTo(x + d + h, y);
      ctx.stroke();
    }
  }
  ctx.restore();
}

// Canvas cannot load the SVG as an image under our CSP (img-src 'self'), so it is drawn directly.
function paint(svg, scale) {
  const box = svg.viewBox.baseVal;
  const canvas = document.createElement('canvas');
  canvas.width = box.width * scale;
  canvas.height = box.height * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale, scale);
  const back = surface(svg);
  ctx.fillStyle = back;
  ctx.fillRect(0, 0, box.width, box.height);
  svg.querySelectorAll('rect, line, polyline, polygon, circle, text').forEach((el) => {
    if (el.closest('defs, pattern')) return;
    const css = getComputedStyle(el);
    if (css.display === 'none') return;
    const stroke = css.stroke !== 'none' ? css.stroke : null;
    const fill = css.fill.startsWith('url') || css.fill === 'none' ? null : css.fill;
    ctx.lineWidth = parseFloat(css.strokeWidth) || 1;
    ctx.setLineDash((css.strokeDasharray || '').split(/[ ,]+/).map(Number).filter((n) => n > 0));
    if (el.tagName === 'text') {
      ctx.font = `${css.fontWeight} ${css.fontSize} ${css.fontFamily}`;
      ctx.textAlign = css.textAnchor === 'end' ? 'right' : css.textAnchor === 'middle' ? 'center' : 'left';
      ctx.fillStyle = fill || '#000';
      const [x, y] = [parseFloat(el.getAttribute('x')), parseFloat(el.getAttribute('y'))];
      const turn = el.getAttribute('transform');
      ctx.save();
      if (turn) {
        const m = /rotate\(\s*(-?[\d.]+)/.exec(turn);
        if (m) {
          ctx.translate(x, y);
          ctx.rotate((parseFloat(m[1]) * Math.PI) / 180);
          ctx.translate(-x, -y);
        }
      }
      ctx.fillText(el.textContent, x, y);
      ctx.restore();
    } else if (el.tagName === 'rect') {
      const [x, y, w, h] = ['x', 'y', 'width', 'height'].map((n) => parseFloat(el.getAttribute(n)));
      const pattern = PATTERNED.exec(el.getAttribute('class') || '');
      if (css.fill.startsWith('url') && pattern) {
        ctx.fillStyle = back;
        ctx.fillRect(x, y, w, h);
        hatch(ctx, x, y, w, h, pattern[1], getComputedStyle(el).getPropertyValue('--rchart-color').trim() || '#000');
      } else if (fill) {
        ctx.fillStyle = fill;
        ctx.fillRect(x, y, w, h);
      }
      if (stroke) {
        ctx.strokeStyle = stroke;
        ctx.strokeRect(x, y, w, h);
      }
    } else if (el.tagName === 'circle') {
      ctx.beginPath();
      ctx.arc(parseFloat(el.getAttribute('cx')), parseFloat(el.getAttribute('cy')), parseFloat(el.getAttribute('r')), 0, 2 * Math.PI);
      if (fill) { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
    } else if (el.tagName === 'line') {
      ctx.beginPath();
      ctx.moveTo(parseFloat(el.getAttribute('x1')), parseFloat(el.getAttribute('y1')));
      ctx.lineTo(parseFloat(el.getAttribute('x2')), parseFloat(el.getAttribute('y2')));
      ctx.strokeStyle = stroke || '#000';
      ctx.stroke();
    } else {
      const points = (el.getAttribute('points') || '').trim().split(/\s+/).map((p) => p.split(',').map(Number));
      ctx.beginPath();
      points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      if (el.tagName === 'polygon') ctx.closePath();
      if (fill && el.tagName === 'polygon') { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
    }
  });
  return canvas;
}

class ReportChart extends HTMLElement {
  connectedCallback() {
    const figure = this.querySelector('figure');
    const frame = this.querySelector('.rchart__frame');
    const svg = this.querySelector('svg.rchart__svg');
    const table = this.querySelector('table');
    if (!figure || !frame || !svg || !table || !table.tBodies.length || this.dataset.ready) return;
    this.dataset.ready = 'true';
    counter += 1;
    this.svg = svg;
    this.frame = frame;
    this.rows = Array.from(table.tBodies[0].rows);
    this.heads = Array.from(table.tHead.rows[0].cells).slice(1).map(text);
    this.current = -1;
    this.name = this.querySelector('figcaption') ? text(this.querySelector('figcaption')) : 'chart';

    this.tip = document.createElement('div');
    this.tip.className = 'rchart__tip';
    this.tip.id = `rchart-tip-${counter}`;
    this.tip.setAttribute('role', 'tooltip');
    this.tip.hidden = true;
    this.status = document.createElement('p');
    this.status.className = 'visually-hidden';
    this.status.setAttribute('role', 'status');
    this.status.setAttribute('aria-live', 'polite');
    frame.appendChild(this.tip);
    figure.appendChild(this.status);

    // The tip replaces the browser's own hover text; the table keeps every number.
    svg.querySelectorAll('[data-i] > title').forEach((t) => t.remove());
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !this.tip.hidden) this.hide();
    });
    this.addToolbar(figure, frame);
    this.addPointer();
    this.addKeyboard();
  }

  addToolbar(figure, frame) {
    const bar = document.createElement('div');
    bar.className = 'rchart__toolbar';
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', this.dataset.toolbar || '');
    if (this.heads.length > 1) {
      this.heads.forEach((label, k) => {
        bar.appendChild(button(label, (e) => {
          const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
          e.currentTarget.setAttribute('aria-pressed', String(on));
          this.svg.querySelectorAll(`[data-s="${k}"]`).forEach((el) => el.classList.toggle('rchart__off', !on));
        }, true));
      });
    }
    bar.appendChild(button(this.dataset.svg || 'SVG', () => this.downloadSvg()));
    bar.appendChild(button(this.dataset.png || 'PNG', () => this.downloadPng()));
    figure.insertBefore(bar, frame);
    if (this.dataset.hint) {
      const hint = document.createElement('p');
      hint.className = 'muted rchart__hint';
      this.hintId = `rchart-hint-${counter}`;
      hint.id = this.hintId;
      hint.textContent = this.dataset.hint;
      figure.insertBefore(hint, frame);
    }
  }

  file() {
    return (this.name || 'chart').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'chart';
  }

  downloadSvg() {
    const xml = new XMLSerializer().serializeToString(standalone(this.svg));
    save(new Blob([xml], { type: 'image/svg+xml' }), `${this.file()}.svg`);
  }

  downloadPng() {
    paint(this.svg, 2).toBlob((blob) => blob && save(blob, `${this.file()}.png`), 'image/png');
  }

  describe(i) {
    const row = this.rows[i];
    if (!row) return '';
    const cells = Array.from(row.cells);
    const parts = cells.slice(1).map((c, k) => (this.heads.length > 1 ? `${this.heads[k]} ${text(c)}` : text(c)));
    return `${text(cells[0])}: ${parts.join(', ')}`;
  }

  mark(i) {
    this.svg.querySelectorAll('.rchart--current').forEach((el) => el.classList.remove('rchart--current'));
    if (i >= 0) this.svg.querySelectorAll(`[data-i="${i}"]`).forEach((el) => el.classList.add('rchart--current'));
  }

  show(i, anchor) {
    const words = this.describe(i);
    if (!words) return;
    this.current = i;
    this.mark(i);
    this.tip.textContent = words;
    this.tip.hidden = false;
    const base = this.frame.getBoundingClientRect();
    const target =
      anchor || Array.from(this.svg.querySelectorAll(`[data-i="${i}"]`)).find((n) => n.getClientRects().length) || this.svg;
    if (!anchor && target.scrollIntoView) target.scrollIntoView({ inline: 'nearest', block: 'nearest' });
    const spot = target.getBoundingClientRect();
    const room = this.frame.scrollLeft + this.frame.clientWidth - this.tip.offsetWidth;
    const left = Math.min(Math.max(0, spot.left - base.left + this.frame.scrollLeft), Math.max(0, room));
    this.tip.style.setProperty('left', `${left}px`);
    this.tip.style.setProperty('top', `${Math.max(0, spot.top - base.top - this.tip.offsetHeight - 4)}px`);
    this.status.textContent = words;
  }

  hide() {
    this.tip.hidden = true;
    this.current = -1;
    this.mark(-1);
  }

  addPointer() {
    this.svg.addEventListener('pointerover', (e) => {
      const point = e.target.closest('[data-i]');
      if (point) this.show(Number(point.dataset.i), point);
    });
    this.svg.addEventListener('pointerleave', () => {
      if (document.activeElement !== this.frame) this.hide();
    });
  }

  addKeyboard() {
    const last = this.rows.length - 1;
    if (this.hintId) this.frame.setAttribute('aria-describedby', this.hintId);
    this.frame.addEventListener('blur', () => this.hide());
    this.frame.addEventListener('keydown', (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (e.key in moves) {
        e.preventDefault();
        this.show(Math.min(last, Math.max(0, this.current + moves[e.key])));
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        this.show(e.key === 'Home' ? 0 : last);
      } else if (e.key === 'Escape') {
        this.hide();
      } else if (e.key === 'Enter' && this.current >= 0) {
        const link = this.rows[this.current].querySelector('a[href]');
        if (link && new URL(link.href).origin === window.location.origin) window.location.assign(link.href);
      }
    });
  }
}

if (!customElements.get('op-report-chart')) customElements.define('op-report-chart', ReportChart);

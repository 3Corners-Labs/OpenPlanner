// ADR 0060 §7 (build step 2), ADR 0118: the open tile of the product mark settles once after a
// sign-in. <op-brand-mark data-sign-in="…"> wraps the server-rendered inline mark; the script
// only adds the class whose CSS animation crosses the grid once, and only the first time this
// tab sees that sign-in (sessionStorage). Nothing happens under prefers-reduced-motion, without
// storage, or without the script: the mark then stays as the server drew it.
const PLAYED = 'op-brand-signed-in';

class BrandMark extends HTMLElement {
  connectedCallback() {
    const signedIn = this.dataset.signIn;
    const mark = this.querySelector('.brand-mark');
    if (!signedIn || !mark) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    try {
      if (window.sessionStorage.getItem(PLAYED) === signedIn) return;
      window.sessionStorage.setItem(PLAYED, signedIn);
    } catch {
      return; // storage refused: never risk replaying on every page
    }
    mark.classList.add('brand-mark--settle');
  }
}

if (!customElements.get('op-brand-mark')) customElements.define('op-brand-mark', BrandMark);

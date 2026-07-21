// The spray-painted poo drummer — a happy pile swinging drumsticks over a drum,
// neon-graffiti style. Injected in more than one place, so filter/gradient ids
// are suffixed to stay unique within the document.

export function pooSvg(suffix: string): string {
  const g = `poo-g-${suffix}`;
  const spray = `poo-spray-${suffix}`;
  const glow = `poo-glow-${suffix}`;
  return `<svg viewBox="0 0 240 270" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#a06a34" />
        <stop offset="1" stop-color="#6b4420" />
      </linearGradient>
      <filter id="${spray}" x="-40%" y="-40%" width="180%" height="180%">
        <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="7" result="n" />
        <feDisplacementMap in="SourceGraphic" in2="n" scale="3.2" />
      </filter>
      <filter id="${glow}" x="-60%" y="-60%" width="220%" height="220%">
        <feDropShadow dx="0" dy="0" stdDeviation="4" flood-color="#ff2e88" flood-opacity="0.85" />
      </filter>
    </defs>
    <g filter="url(#${glow})">
      <g filter="url(#${spray})" stroke-linecap="round" stroke-linejoin="round">
        <g stroke="#2de2ff" stroke-width="3">
          <rect x="66" y="228" width="108" height="26" rx="6" fill="#0d2a30" />
          <ellipse cx="120" cy="228" rx="54" ry="13" fill="#0f3540" />
          <line x1="72" y1="233" x2="88" y2="251" />
          <line x1="120" y1="235" x2="120" y2="254" />
          <line x1="168" y1="233" x2="152" y2="251" />
        </g>
        <path d="M52,214 C34,214 34,190 50,182 C32,176 36,148 60,150 C48,138 54,118 76,122 C66,102 84,86 104,96 C108,78 132,78 136,96 C156,86 172,106 158,124 C182,122 188,150 168,158 C190,164 186,190 168,188 C182,198 176,216 160,214 Z"
              fill="url(#${g})" stroke="#ff2e88" stroke-width="4" />
        <path d="M96,104 C108,96 124,98 128,108" stroke="#c98a4e" stroke-width="4" fill="none" />
        <circle cx="96" cy="150" r="12" fill="#fff" />
        <circle cx="132" cy="150" r="12" fill="#fff" />
        <circle cx="99" cy="152" r="5" fill="#111" />
        <circle cx="135" cy="152" r="5" fill="#111" />
        <path d="M100,176 Q114,190 130,176" stroke="#111" stroke-width="4" fill="none" />
        <g stroke="#9dff3c" stroke-width="5">
          <line x1="60" y1="168" x2="36" y2="122" />
          <line x1="168" y1="168" x2="192" y2="122" />
          <line x1="40" y1="130" x2="15" y2="84" />
          <line x1="188" y1="130" x2="213" y2="84" />
        </g>
        <g stroke="#ffe14c" stroke-width="3">
          <line x1="13" y1="78" x2="5" y2="66" />
          <line x1="20" y1="74" x2="18" y2="60" />
          <line x1="215" y1="78" x2="223" y2="66" />
          <line x1="208" y1="74" x2="210" y2="60" />
        </g>
        <circle cx="48" cy="238" r="4" fill="#ff2e88" />
        <circle cx="196" cy="152" r="3" fill="#2de2ff" />
        <circle cx="30" cy="150" r="3" fill="#9dff3c" />
      </g>
    </g>
  </svg>`;
}

/** Render the poo drummer into `el`, with document-unique ids. */
export function injectPoo(el: HTMLElement, suffix: string): void {
  el.innerHTML = pooSvg(suffix);
}

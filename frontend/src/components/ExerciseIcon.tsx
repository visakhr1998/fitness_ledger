/** Movement-pattern figures.
 *
 * Hevy ships no imagery, so each exercise is drawn as the movement it belongs
 * to. Keying on the pattern means ~20 figures cover all 461 templates, and a
 * template the user invents tomorrow still gets a sensible one. The backend
 * (icons.py) decides which key applies; this file only draws them.
 *
 * Every key produced by icons.py must exist here -- a Python test asserts the
 * backend emits nothing outside its own ALL_ICONS set, and `KNOWN_ICONS` below
 * is the drawing side of that contract.
 */

type Props = { icon: string; size?: number; color?: string };

const S = { stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, fill: "none" };
const HEAD = (cx: number, cy: number, r = 2.1) => <circle cx={cx} cy={cy} r={r} fill="currentColor" />;

/** A loaded bar: two plates and a shaft. */
const Bar = ({ y, x1 = 3, x2 = 21 }: { y: number; x1?: number; x2?: number }) => (
  <>
    <line x1={x1} y1={y} x2={x2} y2={y} {...S} strokeWidth={1.7} />
    <line x1={x1 + 1.5} y1={y - 2.6} x2={x1 + 1.5} y2={y + 2.6} {...S} />
    <line x1={x2 - 1.5} y1={y - 2.6} x2={x2 - 1.5} y2={y + 2.6} {...S} />
  </>
);

const FIGURES: Record<string, JSX.Element> = {
  "bench-press": (<><line x1="4" y1="17" x2="18" y2="17" {...S} />{HEAD(6, 14)}<path d="M8 14.5h7" {...S} /><Bar y={8} x1={5} x2={19} /><path d="M9 13.5 8 9M15 13.5l1-4.5" {...S} /></>),
  pushup: (<>{HEAD(5.5, 9)}<path d="M7.5 10.5 17 14M7.5 10.5 6 17M17 14l3 3M12 12.2 11 18" {...S} /><line x1="3" y1="19.5" x2="21" y2="19.5" {...S} /></>),
  "overhead-press": (<>{HEAD(12, 16.5)}<Bar y={5} x1={4} x2={20} /><path d="M9 6.5 10.5 14M15 6.5 13.5 14M10.5 18.5v3M13.5 18.5v3" {...S} /></>),
  "lateral-raise": (<>{HEAD(12, 6)}<path d="M12 8.5v7M12 10.5 5 13M12 10.5l7 2.5M10.5 15.5 9 21M13.5 15.5 15 21" {...S} /><circle cx="4" cy="13.4" r="1.6" fill="currentColor" /><circle cx="20" cy="13.4" r="1.6" fill="currentColor" /></>),
  "chest-fly": (<>{HEAD(12, 15.5)}<path d="M12 17.5h0" {...S} /><path d="M12 15.5 4 9M12 15.5 20 9" {...S} /><circle cx="3.4" cy="8.2" r="1.7" fill="currentColor" /><circle cx="20.6" cy="8.2" r="1.7" fill="currentColor" /></>),
  pullup: (<><line x1="3" y1="4" x2="21" y2="4" {...S} /><path d="M9 4.5v3.5M15 4.5v3.5" {...S} />{HEAD(12, 10)}<path d="M12 12.2v5M9 8 12 12.2 15 8M10.5 20l1.5-2.8 1.5 2.8" {...S} /></>),
  pulldown: (<><line x1="4" y1="3.5" x2="20" y2="3.5" {...S} /><path d="M12 3.5v3" {...S} /><path d="M7 7h10" {...S} />{HEAD(12, 12)}<path d="M8 7.5 11 11M16 7.5 13 11M12 14v4M10 21l2-3 2 3" {...S} /></>),
  row: (<>{HEAD(7, 8)}<path d="M8.5 9.5 12 12M12 12h6M18 10v4M8 10.5 6.5 17M12 12l-2 5" {...S} /><line x1="3" y1="19.5" x2="21" y2="19.5" {...S} /></>),
  shrug: (<>{HEAD(12, 5.5)}<path d="M7.5 9.5h9M8 9.5 8 17M16 9.5 16 17" {...S} /><circle cx="8" cy="18.6" r="1.7" fill="currentColor" /><circle cx="16" cy="18.6" r="1.7" fill="currentColor" /></>),
  deadlift: (<>{HEAD(12, 5)}<path d="M12 7.2v4.5M12 11.7 9 16M12 11.7l3 4.3M9 16v4M15 16v4" {...S} /><Bar y={13.5} x1={4} x2={20} /></>),
  squat: (<>{HEAD(12, 5)}<Bar y={8} x1={4} x2={20} /><path d="M12 9.5v3.5M12 13 8.5 16v4M12 13l3.5 3v4" {...S} /></>),
  lunge: (<>{HEAD(10, 5)}<path d="M10 7v4.5M10 11.5 5.5 19M10 11.5 16 16v4M4 20h3" {...S} /></>),
  "leg-extension": (<><path d="M5 6h8v7" {...S} />{HEAD(7, 4)}<path d="M13 13l6-2" {...S} /><circle cx="20" cy="10.6" r="1.7" fill="currentColor" /><line x1="4" y1="20" x2="16" y2="20" {...S} /></>),
  "leg-curl": (<><path d="M5 12h9" {...S} />{HEAD(6, 9.6)}<path d="M14 12c3 0 4 2 3.5 5" {...S} /><circle cx="17" cy="18.4" r="1.7" fill="currentColor" /></>),
  "hip-thrust": (<><path d="M4 16h5l3-4 4 2" {...S} />{HEAD(4.5, 13.6)}<Bar y={10.5} x1={9} x2={19} /><line x1="3" y1="19.5" x2="21" y2="19.5" {...S} /></>),
  "calf-raise": (<>{HEAD(12, 4.5)}<path d="M12 6.6v7M12 13.6 10 18M12 13.6 14 18" {...S} /><path d="M8 20h3M13 20h3" {...S} /></>),
  "biceps-curl": (<>{HEAD(12, 5)}<path d="M12 7.2v6M12 9.5 8.5 12l1.5 3M12 9.5l3.5 2.5-1.5 3" {...S} /><circle cx="9.4" cy="16.4" r="1.8" fill="currentColor" /><circle cx="14.6" cy="16.4" r="1.8" fill="currentColor" /></>),
  triceps: (<>{HEAD(12, 5)}<path d="M12 7.2v5M9 12h6M10 15l-.5 5M14 15l.5 5" {...S} /><line x1="8" y1="12" x2="16" y2="12" {...S} strokeWidth={2.4} /></>),
  wrist: (<><path d="M5 12h9a3 3 0 0 1 0 6H8" {...S} /><circle cx="18" cy="15" r="2" fill="currentColor" /><path d="M5 9v6" {...S} /></>),
  crunch: (<>{HEAD(7, 9)}<path d="M8.5 10.5 13 13l4-1M13 13l-1 5" {...S} /><line x1="3" y1="19.5" x2="21" y2="19.5" {...S} /><path d="M17 19.5 15 15" {...S} /></>),
  neck: (<>{HEAD(12, 6)}<path d="M12 8.4v3.6M8 12h8v6H8z" {...S} /></>),
  run: (<>{HEAD(14.5, 4)}<path d="M13 7.5 9.5 9.8l-1.6 3.6M13 7.5l3.4 2.3 1.1 3.4M13 7.5l-1.2 4.7 2.9 2.4.9 4.9M11.8 12.2 8 14.2l-2.4 4" {...S} /></>),
  bike: (<><circle cx="5.5" cy="16.5" r="3.5" {...S} /><circle cx="18.5" cy="16.5" r="3.5" {...S} /><path d="M5.5 16.5 9 9h5l4.5 7.5M9 9h4M14 9l1.5 3" {...S} /></>),
  "row-machine": (<><path d="M3 18h18" {...S} />{HEAD(8, 11)}<path d="M9.5 12.5 14 14l4-3" {...S} /><path d="M14 14v4" {...S} /></>),
  swim: (<><path d="M3 17c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0" {...S} /><path d="M3 20c2 1.3 4 1.3 6 0s4-1.3 6 0 4 1.3 6 0" {...S} />{HEAD(9, 8)}<path d="M11 9.5 17 7" {...S} /></>),
  cardio: (<><path d="M3 13h4l2-5 3 9 2.5-6 1.5 2h5" {...S} /></>),
  dumbbell: (<><line x1="8" y1="12" x2="16" y2="12" {...S} strokeWidth={2} /><path d="M6 9v6M4 10.5v3M18 9v6M20 10.5v3" {...S} /></>),
};

export const KNOWN_ICONS = Object.keys(FIGURES);

export function ExerciseIcon({ icon, size = 28, color }: Props) {
  const figure = FIGURES[icon] ?? FIGURES.dumbbell;
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24"
      style={{ color: color ?? "var(--accent)", flex: "0 0 auto" }}
      role="img" aria-label={icon.replace(/-/g, " ")}
    >
      {figure}
    </svg>
  );
}

/** The coach. An owl, because the insights are observations rather than orders. */
export function CoachMascot({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" role="img" aria-label="Coach">
      <circle cx="20" cy="21" r="14" fill="var(--accent-soft)" />
      <path d="M8.5 12.5 13 8l3 4M31.5 12.5 27 8l-3 4" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
      <circle cx="15" cy="19" r="4.6" fill="var(--surface)" stroke="var(--accent)" strokeWidth="1.6" />
      <circle cx="25" cy="19" r="4.6" fill="var(--surface)" stroke="var(--accent)" strokeWidth="1.6" />
      <circle cx="15.6" cy="19" r="1.9" fill="var(--accent)" />
      <circle cx="24.4" cy="19" r="1.9" fill="var(--accent)" />
      <path d="M20 23.5 18 26h4z" fill="var(--accent-alt)" />
      <path d="M12 29c2.6 2.4 13.4 2.4 16 0" fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

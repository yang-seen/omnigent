import type { SVGProps } from "react";

// Hermes Agent (Nous Research) glyph. No brand mark ships for Hermes, so this
// is an original winged-staff emblem — the winged messenger — drawn in
// currentColor so it follows the app theme like its sibling icons. The wing is
// authored once and mirrored around the x=12 center for exact symmetry.
export function HermesIcon(props: SVGProps<SVGSVGElement>) {
  // One wing: three feathers fanning up-and-out from beside the staff.
  const wing =
    "M11.3 8.2 C 8.8 6.6 6.3 6.4 4.5 7.2 " +
    "M11.3 9.7 C 9 8.6 6.5 8.7 4.8 9.9 " +
    "M11.3 11.2 C 9.2 10.5 7 11 5.6 12.3";
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {/* staff head */}
      <circle cx="12" cy="4.6" r="1.3" fill="currentColor" stroke="none" />
      {/* staff */}
      <path d="M12 5.9 V 19" />
      {/* left wing */}
      <path d={wing} />
      {/* right wing — the left wing mirrored around the x=12 center */}
      <g transform="matrix(-1 0 0 1 24 0)">
        <path d={wing} />
      </g>
    </svg>
  );
}

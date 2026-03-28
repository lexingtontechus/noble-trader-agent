export default function Logo({ size, padding }) {
  return (
    <svg
      className={`mask mask-circle size-${size} p-${padding}`}
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 312.5 312.5"
      preserveAspectRatio="xMidYMid meet"
      height={`${size}`}
      width={`${size}`}
    >
      <g
        featurekey="rootContainer"
        transform="matrix(6.25,0,0,6.25,0,0)"
        fill="#FFFF52"
      >
        <circle cx="25" cy="25" r="25" fill="#FFFF52" />
      </g>
      <g
        featurekey="nameFeature-0"
        transform="matrix(4.6838406657263025,0,0,4.6838406657263025,37.26463733709479,39.16159334273698)"
        fill="#D32000"
      >
        <path
          d="M11 40 l-7 0 l0 -30 l5 0 l7 12.4 l0 -12.4 l7 0 l0 30 l-5 0 l-7 -12.4 l0 12.4 z M7 18.48 l11.52 20.52 l2.28 0 l-15.8 -28 l0 28 l2 0 l0 -20.52 z M6.2 11 l15.8 28 l0 -28 l-2 0 l0 20.52 l-11.52 -20.52 l-2.28 0 z M17 24.119999999999997 l2 3.48 l0 -16.6 l-2 0 l0 13.12 z M10 25.8 l-2 -3.48 l0 16.68 l2 0 l0 -13.2 z M32.7 40 l0 -23 l-7 0 l0 -7 l21 0 l0 7 l-7 0 l0 23 l-7 0 z M33.7 39 l2 0 l0 -26 l10 0 l0 -2 l-19 0 l0 2 l7 0 l0 26 z M32.7 14 l-6 0 l0 2 l6 0 l0 -2 z M45.7 14 l-8.28 0 l1.96 2 l6.32 0 l0 -2 z M38.7 16.8 l-2 -2.08 l0 24.28 l2 0 l0 -22.2 z"
          fill="#D32000"
        />
      </g>
    </svg>
  );
}

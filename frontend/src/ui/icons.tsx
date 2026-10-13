// One icon set: inline SVG on a 16-unit grid, stroke 1.5, currentColor.
// No emoji anywhere in this application (DESIGN § 9, rule 6).


interface IconProps {
  size?: number;
  className?: string;
  title?: string;
}

function base(size: number, className: string | undefined, title: string | undefined) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    'aria-hidden': title ? undefined : true,
    role: title ? 'img' : undefined,
    focusable: false,
  };
}

const titleNode = (title?: string) => (title ? <title>{title}</title> : null);

export function IconCheck({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M3 8.5 6.2 11.7 13 4.5" />
    </svg>
  );
}

export function IconX({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function IconFlag({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M4 14V2.5M4 3h7.5l-1.4 2.8 1.4 2.7H4" />
    </svg>
  );
}

export function IconAlert({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M8 2.6 14.2 13H1.8L8 2.6Z" />
      <path d="M8 6.6v3.1M8 11.6v.01" />
    </svg>
  );
}

export function IconOpen({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <circle cx="8" cy="8" r="5.4" />
    </svg>
  );
}

export function IconPause({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M6 3.5v9M10 3.5v9" />
    </svg>
  );
}

export function IconPlay({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M5 3.2 12.5 8 5 12.8V3.2Z" />
    </svg>
  );
}

export function IconCopy({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5V4a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5" />
    </svg>
  );
}

export function IconDownload({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M8 2.5v7.5M4.8 7.3 8 10.5l3.2-3.2M3 13h10" />
    </svg>
  );
}

export function IconSun({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1" />
    </svg>
  );
}

export function IconMoon({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M13 9.6A5.6 5.6 0 0 1 6.4 3a5.6 5.6 0 1 0 6.6 6.6Z" />
    </svg>
  );
}

export function IconHelp({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <circle cx="8" cy="8" r="5.6" />
      <path d="M6.3 6.2A1.8 1.8 0 0 1 8 4.9c1 0 1.8.7 1.8 1.6 0 1.3-1.8 1.4-1.8 2.7" />
      <path d="M8 11.6v.01" />
    </svg>
  );
}

export function IconKey({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <circle cx="5.2" cy="8" r="2.7" />
      <path d="M7.9 8H14M11.4 8v2.2M13.2 8v1.6" />
    </svg>
  );
}

export function IconShield({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M8 1.9 13 3.6v4c0 3-2.1 5.2-5 6.5-2.9-1.3-5-3.5-5-6.5v-4L8 1.9Z" />
      <path d="M5.9 7.9 7.4 9.4l2.9-2.9" />
    </svg>
  );
}

export function IconRefresh({ size = 16, className, title }: IconProps) {
  return (
    <svg {...base(size, className, title)}>
      {titleNode(title)}
      <path d="M13 8a5 5 0 1 1-1.6-3.7" />
      <path d="M13.2 2.6v2.8h-2.8" />
    </svg>
  );
}

/** The product mark: one span marked on a track. */
export function MarkLongWatch({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <rect x="2" y="6.5" width="20" height="11" rx="2.5" stroke="var(--border-strong)" strokeWidth="1.5" />
      <path d="M6 6.5v11M18 6.5v11" stroke="var(--border-strong)" strokeWidth="1.5" />
      <rect x="9" y="9" width="6" height="6" rx="1" fill="var(--accent)" />
    </svg>
  );
}

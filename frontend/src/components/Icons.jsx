/**
 * Small hand-written inline SVG icon set (stroke style, 20x20) -- kept
 * dependency-free on purpose (no icon font/library CDN needed for half a
 * dozen icons, and no extra network request beyond what's already here).
 */
function iconProps(props) {
  return {
    width: 20, height: 20, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round",
    ...props,
  };
}

function IconDashboard(props) {
  return (
    <svg {...iconProps(props)}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  );
}

function IconReports(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M4 19V10" /><path d="M10 19V5" /><path d="M16 19v-7" />
      <path d="M3 19h18" />
    </svg>
  );
}

function IconQueue(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  );
}

function IconPortal(props) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1" />
    </svg>
  );
}

function IconPipeline(props) {
  return (
    <svg {...iconProps(props)}>
      <rect x="3" y="4" width="5" height="16" rx="1.5" />
      <rect x="10" y="4" width="5" height="11" rx="1.5" />
      <rect x="17" y="4" width="5" height="7" rx="1.5" />
    </svg>
  );
}

function IconLogout(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

function IconSearch(props) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}

function IconUsers(props) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20v-1.5A5.5 5.5 0 0 1 8 13h2a5.5 5.5 0 0 1 5.5 5.5V20" />
      <path d="M16 8.5a3 3 0 1 1 1 5.83" />
      <path d="M21.5 20v-1.2a4.3 4.3 0 0 0-3-4.1" />
    </svg>
  );
}

function IconShield(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M12 3l7 3v6c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V6l7-3z" />
      <path d="M9.5 12l1.8 1.8L15 10" />
    </svg>
  );
}

function IconPulse(props) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M7 12h2.5l1.5-4 3 8 1.5-4H17" />
    </svg>
  );
}

function IconCloud(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M7 18h10a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.7-1.7A4 4 0 0 0 7 18z" />
    </svg>
  );
}

function IconMail(props) {
  return (
    <svg {...iconProps(props)}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  );
}

function IconKey(props) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="8" cy="8" r="4" />
      <path d="M11 11l8 8M16 16l2-2M18 18l2-2" />
    </svg>
  );
}

/** Mobile-only: opens/closes the sidebar drawer (see App.jsx's
 * "sidebar-open" state and styles.css's <=900px rules). */
function IconMenu(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function IconClose(props) {
  return (
    <svg {...iconProps(props)}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

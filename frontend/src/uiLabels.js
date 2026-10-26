/**
 * Presentation-only tier labels -- reads as an AI SUGGESTION a human
 * reviewer can accept or override, never a command already carried out.
 * This is a DISPLAY mapping only: the real stored/filtered/API value
 * (ranking_tier) is unchanged ("Recommended for Fast-Track" /
 * "To be Considered" / "Rejected") everywhere a control actually reads
 * or sends that value (tier <select> filters, review decision calls,
 * chart data keys) -- only changed where a tier is rendered as
 * read-only text/badge for a person to read. See ReviewQueue.jsx's own
 * existing copy ("The AI only recommends a tier -- approve/reject here
 * ... is what actually decides") for the same framing this extends to
 * every other tier label in the app.
 */
var TIER_DISPLAY_LABELS = {
  "Recommended for Fast-Track": "AI Suggests: Fast-Track",
  "To be Considered": "AI Suggests: Consider Further",
  "Rejected": "AI Suggests: Not a Match",
};

function tierDisplayLabel(tier) {
  return TIER_DISPLAY_LABELS[tier] || tier;
}

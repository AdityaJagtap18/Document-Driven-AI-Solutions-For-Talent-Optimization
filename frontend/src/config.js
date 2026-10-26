/**
 * Switch "mode" to "real" and fill in baseUrl/functionKey once the Azure
 * Functions API (azure-functions/) is actually deployed or running locally
 * via `func start`. Everything else in the app is unaffected by this
 * switch — App.jsx picks window.CDPI_REAL_API or window.CDPI_MOCK_API
 * based on `mode` alone.
 */
window.CDPI_CONFIG = {
  mode: "real", // "mock" | "real" -- real Functions host at localhost:7071 once it's running (see docs/fde/DECISIONS_AND_BLOCKERS.md for a known local-startup auth issue)
  baseUrl: "http://localhost:7071",
  functionKey: "",
};

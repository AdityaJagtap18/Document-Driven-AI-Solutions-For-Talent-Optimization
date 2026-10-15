/**
 * Switch "mode" to "real" and fill in baseUrl/functionKey once the Azure
 * Functions API (azure-functions/) is actually deployed or running locally
 * via `func start`. Everything else in the app is unaffected by this
 * switch — App.jsx picks window.CDPI_REAL_API or window.CDPI_MOCK_API
 * based on `mode` alone.
 */
window.CDPI_CONFIG = {
  mode: "mock", // "mock" | "real"
  baseUrl: "http://localhost:7071",
  functionKey: "",
};

/**
 * Tiny in-memory, per-browser-tab TTL cache for the Azure-backed admin
 * pages (Azure Readiness, Azure Service Hub, Azure Observability Report).
 * These pages' real data sources are genuinely slow -- live `az` CLI
 * calls and real Azure Monitor/Log Analytics queries, both observed to
 * take several real seconds -- and several tabs/sections on the same
 * page call the same endpoint independently, plus switching tabs
 * unmounts/remounts a component and re-fires its useEffect, re-paying
 * that cost every time with nothing new to show.
 *
 * Deliberately NOT used for anything time-sensitive or write-driven
 * (candidate data, review decisions, pipeline stages) -- only for the
 * read-only Azure status/telemetry views where a few seconds of
 * staleness is an acceptable, explicit tradeoff for not re-showing a
 * loading spinner on every tab switch. A manual "Refresh" button on
 * each page explicitly invalidates its own keys and bypasses this.
 *
 * Stores the in-flight PROMISE (not just the resolved value), so two
 * components requesting the same key within the same tick (e.g. two
 * widgets both wanting azure-readiness) share one real network call
 * instead of firing two.
 */
(function () {
  var DEFAULT_TTL_MS = 60000; // matches the backend's own observability-report cache TTL
  var store = {}; // key -> { expiresAt, promise }

  function get(key, fetchFn, ttlMs) {
    var entry = store[key];
    var now = Date.now();
    if (entry && entry.expiresAt > now) {
      return entry.promise;
    }
    var promise = fetchFn().catch(function (err) {
      delete store[key]; // never cache a rejected call -- the next request should retry for real
      throw err;
    });
    store[key] = { expiresAt: now + (ttlMs || DEFAULT_TTL_MS), promise: promise };
    return promise;
  }

  function invalidate(key) {
    delete store[key];
  }

  function invalidatePrefix(prefix) {
    Object.keys(store).forEach(function (key) {
      if (key.indexOf(prefix) === 0) delete store[key];
    });
  }

  window.CDPI_AZURE_CACHE = { get: get, invalidate: invalidate, invalidatePrefix: invalidatePrefix };
})();

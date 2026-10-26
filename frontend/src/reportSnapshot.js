/**
 * Persistent (localStorage), explicit-refresh-only data loading for the
 * REPORTING pages (Admin Reports, Recruiter Reports, Recruitment
 * Operations) -- per explicit instruction: a report shows its last known
 * snapshot immediately on every visit, never a loading spinner, and only
 * re-fetches when the user clicks "Refresh". Same "store the last real
 * result, only refetch on explicit request" principle this app already
 * used for the Azure Observability Report (see observability_snapshots.py
 * / docs/AZURE_OBSERVABILITY.md), generalized here as a small reusable
 * frontend hook instead of a one-off.
 *
 * Deliberately NOT used on operational/action pages (Pipeline, Review
 * Queue, Candidate Portal, Dashboard's candidate table) -- those drive
 * real decisions (approve/reject, stage changes, interviewer assignment)
 * against the CURRENT candidate list; silently showing yesterday's
 * snapshot there risks a recruiter acting on stale data. Reports (read-
 * only aggregates/charts) are the right scope for this.
 */
(function () {
  var PREFIX = "cdpi_report_snapshot:";

  function load(key) {
    try {
      var raw = localStorage.getItem(PREFIX + key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function save(key, data) {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify({ data: data, savedAt: new Date().toISOString() }));
    } catch (e) {
      // localStorage unavailable/full -- the report still works this
      // session, it just won't have a snapshot to show next visit.
    }
  }

  /**
   * React hook. `key` must be stable and unique per report (e.g.
   * "admin_reports"). `fetchFn` returns a Promise resolving to whatever
   * shape the caller wants cached. Returns { state, refresh }:
   *   state = { loading, error, data, lastUpdated }
   *   refresh() -- forces a real fetch, overwrites the snapshot + state.
   *
   * `loading` is true ONLY the very first time a report is ever opened
   * (no snapshot exists yet) or during an explicit refresh() -- never on
   * an ordinary remount/tab-switch once a snapshot exists.
   */
  function useReportSnapshot(key, fetchFn) {
    var existing = load(key);
    var state = React.useState(
      existing
        ? { loading: false, error: null, data: existing.data, lastUpdated: existing.savedAt }
        : { loading: true, error: null, data: null, lastUpdated: null },
    );
    var data = state[0];
    var setData = state[1];

    function refresh() {
      setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
      return fetchFn()
        .then(function (result) {
          var now = new Date().toISOString();
          save(key, result);
          setData({ loading: false, error: null, data: result, lastUpdated: now });
        })
        .catch(function (err) {
          setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
        });
    }

    React.useEffect(function () {
      if (!existing) refresh();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return { state: data, refresh: refresh };
  }

  window.CDPI_REPORT_SNAPSHOT = { load: load, save: save, useReportSnapshot: useReportSnapshot };
})();

import type { HydratedResultsSuccess } from "../types";
import StatusBadge from "./StatusBadge";

type HydratedResultSummaryProps = {
  results: HydratedResultsSuccess;
  selectedMode?: number;
  onSelectMode?: (mode: number) => void;
};

function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : "—";
}

export default function HydratedResultSummary({
  results,
  selectedMode,
  onSelectMode,
}: HydratedResultSummaryProps) {
  return (
    <section
      aria-labelledby="hydrated-result-summary-title"
      className="hydrated-result-inline"
    >
      <div className="hydrated-section-heading">
        <div>
          <span>水分子后处理</span>
          <h2 id="hydrated-result-summary-title">逐构象水分子统计</h2>
        </div>
        <StatusBadge tone="warning">处理后评分未计算</StatusBadge>
      </div>
      <p className="hydrated-result-semantics">
        Raw AD4 affinity 保持不变；强、弱、置换与保留水仅用于本次
        run 的结构解释。
      </p>
      <div className="hydrated-water-summary">
        <article>
          <span>原始候选水</span>
          <strong>{count(results.water_summary.raw_water_count)}</strong>
        </article>
        <article>
          <span>保留水</span>
          <strong>{count(results.water_summary.retained_water_count)}</strong>
        </article>
        <article>
          <span>强水</span>
          <strong>{count(results.water_summary.strong_water_count)}</strong>
        </article>
        <article>
          <span>弱水</span>
          <strong>{count(results.water_summary.weak_water_count)}</strong>
        </article>
        <article>
          <span>置换水</span>
          <strong>{count(results.water_summary.displaced_water_count)}</strong>
        </article>
      </div>
      <div className="hydrated-results-table-wrap">
        <table className="hydrated-results-table">
          <thead>
            <tr>
              <th scope="col">Mode</th>
              <th scope="col">Raw AD4 affinity</th>
              <th scope="col">保留水</th>
              <th scope="col">强水</th>
              <th scope="col">弱水</th>
              <th scope="col">置换水</th>
            </tr>
          </thead>
          <tbody>
            {results.modes.map((mode) => (
              <tr
                className={selectedMode === mode.mode ? "is-selected" : ""}
                key={mode.mode}
              >
                <th scope="row">
                  {onSelectMode ? (
                    <button
                      aria-pressed={selectedMode === mode.mode}
                      onClick={() => onSelectMode(mode.mode)}
                      type="button"
                    >
                      Mode {mode.mode}
                    </button>
                  ) : (
                    <>Mode {mode.mode}</>
                  )}
                </th>
                <td>{mode.raw_affinity_kcal_mol} kcal/mol</td>
                <td>
                  {count(mode.water_summary.retained_water_count)}
                </td>
                <td>{count(mode.water_summary.strong_water_count)}</td>
                <td>{count(mode.water_summary.weak_water_count)}</td>
                <td>
                  {count(mode.water_summary.displaced_water_count)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

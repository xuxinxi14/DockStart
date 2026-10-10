import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import type { ScreeningArchiveComparison } from "../src/components/ScreeningArchiveComparisonView.tsx";
import {
  comparisonItemDisplayName,
  filterScreeningArchiveComparisonRows,
} from "../src/utils/screeningArchiveComparison.ts";

test("Language switching keeps rendered ligand identities aligned with search", async () => {
  const names = ["配体", "示例项目", "constructor", "__proto__"];
  const comparison: ScreeningArchiveComparison = {
    baseline_archive: { archive_id: "baseline", screening_id: "screening_001" },
    comparison_archive: { archive_id: "comparison", screening_id: "screening_002" },
    comparability: { status: "comparable", direct_score_comparison: true },
    counts: { baseline_total: names.length, comparison_total: names.length, matched: names.length },
    rows: names.map((name, index) => ({
      identity_sha256: String(index).repeat(64),
      match_status: "matched",
      baseline_items: [{ item_id: `item_${index}`, display_label: name, best_affinity_kcal_mol: -7.5, rank: index + 1 }],
      comparison_items: [{ item_id: `other_${index}`, display_label: name, best_affinity_kcal_mol: -7.5, rank: index + 1 }],
      score_delta_kcal_mol: 0,
      rank_delta: 0,
    })),
  };
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
    plugins: [{
      name: "language-test-icons",
      enforce: "pre",
      resolveId(source) {
        return source === "@phosphor-icons/react" ? "\0language-test-icons" : null;
      },
      load(id) {
        if (id !== "\0language-test-icons") return null;
        const icons = ["ArrowDown", "ArrowLeft", "ArrowUp", "CaretLeft", "CaretRight", "CheckCircle", "MagnifyingGlass", "Scales", "WarningCircle"];
        return `import React from "react"; const Icon = () => React.createElement("svg"); ${icons.map(name => `export const ${name} = Icon;`).join(" ")}`;
      },
    }],
  });
  try {
    const language = await vite.ssrLoadModule("/src/i18n/language.ts");
    const { translate } = await vite.ssrLoadModule("/src/i18n/translate.ts");
    const { default: View } = await vite.ssrLoadModule("/src/components/ScreeningArchiveComparisonView.tsx");
    for (const locale of ["zh-CN", "en-US", "zh-CN"]) {
      language.setLanguage(locale);
      const html = renderToStaticMarkup(React.createElement(View, { comparison }));
      assert.ok(html.includes(translate("逐配体结果")));
      for (const name of names) {
        assert.ok(html.includes(`<strong title="${name}">${name}</strong>`), `${locale}: ${name}`);
        assert.equal(filterScreeningArchiveComparisonRows(comparison.rows, { query: name }).length, 1);
      }
    }
    assert.equal(comparisonItemDisplayName(comparison.rows[0].baseline_items[0]), "配体");
    assert.deepEqual(comparison.rows.map(row => row.baseline_items[0].display_label), names);
  } finally {
    await vite.close();
  }
});

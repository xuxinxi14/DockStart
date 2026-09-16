import assert from "node:assert/strict";
import test from "node:test";

import {
  buildFieldHintLabel,
  isTooltipAvailable,
  mergeAriaDescribedBy,
} from "../src/components/tooltipAccessibility.ts";

test("tooltip availability rejects explicit, child, and empty-label disablement", () => {
  assert.equal(
    isTooltipAvailable({ disabled: false, childDisabled: false, label: "帮助" }),
    true,
  );
  assert.equal(
    isTooltipAvailable({ disabled: true, childDisabled: false, label: "帮助" }),
    false,
  );
  assert.equal(
    isTooltipAvailable({ disabled: false, childDisabled: true, label: "帮助" }),
    false,
  );
  assert.equal(
    isTooltipAvailable({ disabled: false, childDisabled: false, label: "  " }),
    false,
  );
});

test("aria-describedby preserves existing descriptions and adds one tooltip id", () => {
  assert.equal(mergeAriaDescribedBy(undefined, "tooltip-1"), "tooltip-1");
  assert.equal(
    mergeAriaDescribedBy("field-help validation-help", "tooltip-1"),
    "field-help validation-help tooltip-1",
  );
  assert.equal(
    mergeAriaDescribedBy("field-help tooltip-1", "tooltip-1"),
    "field-help tooltip-1",
  );
});

test("field hint accessible name names the parameter it explains", () => {
  assert.equal(buildFieldHintLabel("搜索彻底程度"), "查看“搜索彻底程度”的说明");
  assert.equal(buildFieldHintLabel("  搜索范围（Box）  "), "查看“搜索范围（Box）”的说明");
  assert.equal(buildFieldHintLabel(""), "查看参数说明");
  assert.equal(buildFieldHintLabel("   "), "查看参数说明");
});

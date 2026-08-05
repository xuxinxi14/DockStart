import assert from "node:assert/strict";
import test from "node:test";

import {
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

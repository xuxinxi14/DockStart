import * as $3Dmol from "3dmol";
import type { BoxVisualizationPayload } from "../types";

/** Add a model-space XYZ triad near the search box without altering molecular models. */
export function addOrientationAxes(
  viewer: ReturnType<typeof $3Dmol.createViewer>,
  visualization: BoxVisualizationPayload | null,
): void {
  const axisLength = visualization
    ? Math.max(3, Math.min(8, Math.max(visualization.size_x, visualization.size_y, visualization.size_z) * 0.18))
    : 4;
  const origin = visualization
    ? {
        x: visualization.min.x - axisLength * 0.35,
        y: visualization.min.y - axisLength * 0.35,
        z: visualization.min.z - axisLength * 0.35,
      }
    : { x: 0, y: 0, z: 0 };
  const axes = [
    { label: "X", color: "#ff5f62", end: { ...origin, x: origin.x + axisLength } },
    { label: "Y", color: "#58d68d", end: { ...origin, y: origin.y + axisLength } },
    { label: "Z", color: "#54a9ff", end: { ...origin, z: origin.z + axisLength } },
  ];

  for (const axis of axes) {
    viewer.addArrow({
      start: origin,
      end: axis.end,
      radius: Math.max(0.08, axisLength * 0.025),
      radiusRatio: 1.9,
      mid: 0.72,
      color: axis.color,
    });
    viewer.addLabel(axis.label, {
      position: axis.end,
      fontSize: 13,
      fontColor: axis.color,
      backgroundColor: "#061d33",
      backgroundOpacity: 0.88,
      borderColor: axis.color,
      borderThickness: 1,
      inFront: true,
      alignment: "center",
    });
  }
}

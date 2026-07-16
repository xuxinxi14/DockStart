export type RawToPreparedWorkflow = {
  acquireRaw: () => Promise<boolean>;
  observerIsCurrent: () => boolean;
  prepareRaw: (overwritePrepared: boolean) => Promise<boolean>;
};

/**
 * Keeps the scientific write sequence independent from the page lifecycle.
 *
 * The page may stop observing progress after navigation, but once the user has
 * explicitly started an acquisition/import, a successful raw write must still
 * be followed by PDBQT preparation. Destructive overwrite permission does not
 * survive page disposal: a detached continuation must preserve any PDBQT the
 * user imports from a later page.
 */
export async function runRawToPreparedWorkflow({
  acquireRaw,
  observerIsCurrent,
  prepareRaw,
}: RawToPreparedWorkflow): Promise<boolean> {
  if (!await acquireRaw()) return false;
  return prepareRaw(observerIsCurrent());
}

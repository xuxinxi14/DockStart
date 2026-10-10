import { useSyncExternalStore } from "react";
import { DEFAULT_LANGUAGE, getLanguage, subscribeLanguage } from "./language";

export function useLanguage() {
  return useSyncExternalStore(subscribeLanguage, getLanguage, () => DEFAULT_LANGUAGE);
}

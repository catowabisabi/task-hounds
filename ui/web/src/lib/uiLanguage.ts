import { useEffect, useState } from "react";
import { apiGet } from "./api";

export const UI_LANGUAGE_CHANGED = "task-hounds-language-changed";

const thinkingCopy = [
  "Thinking...",
  "Thinking",
  "On",
  "Off",
  "OpenCode runs include thinking",
  "OpenCode runs omit thinking",
] as const;

export function thinkingLabels(_language: string) {
  void _language;
  const copy = thinkingCopy;
  return {
    thinking: copy[0],
    toggle: copy[1],
    on: copy[2],
    off: copy[3],
    enabledTitle: copy[4],
    disabledTitle: copy[5],
  };
}

export function useUiLanguage(): string {
  const [language, setLanguage] = useState("en");

  useEffect(() => {
    apiGet<{ language?: string }>("/api/settings")
      .then(settings => settings.language && setLanguage(settings.language))
      .catch(() => {});

    const onChange = (event: Event) => {
      const next = (event as CustomEvent<{ language?: string }>).detail?.language;
      if (next) setLanguage(next);
    };
    window.addEventListener(UI_LANGUAGE_CHANGED, onChange);
    return () => window.removeEventListener(UI_LANGUAGE_CHANGED, onChange);
  }, []);

  return language;
}

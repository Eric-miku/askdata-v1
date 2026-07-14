import type { ThemeMode } from "./types/query";

const THEME_STORAGE_KEY = "askdata-theme";

interface ThemeStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}

export function getInitialTheme(storage: ThemeStorage = localStorage): ThemeMode {
  return storage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
}

export function saveTheme(
  theme: ThemeMode,
  storage: ThemeStorage = localStorage,
): void {
  storage.setItem(THEME_STORAGE_KEY, theme);
}

export function applyTheme(theme: ThemeMode): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

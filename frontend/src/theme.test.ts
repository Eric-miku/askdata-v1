import { describe, expect, it } from "vitest";
import { getInitialTheme, saveTheme } from "./theme";

describe("theme preferences", () => {
  it("defaults to dark and restores a saved light preference", () => {
    const storage = {
      getItem: () => null,
      setItem: () => undefined,
    };
    expect(getInitialTheme(storage)).toBe("dark");

    const savedStorage = {
      getItem: () => "light",
      setItem: () => undefined,
    };
    expect(getInitialTheme(savedStorage)).toBe("light");
  });

  it("persists the selected theme", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };

    saveTheme("light", storage);

    expect(values.get("askdata-theme")).toBe("light");
  });
});

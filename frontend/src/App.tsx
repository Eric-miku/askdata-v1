import { useState } from "react";
import HistorySidebar from "./components/HistorySidebar";
import { QueryResultDemo } from "./pages/QueryResultDemo";
import { getInitialTheme, saveTheme, applyTheme } from "./theme";

export default function App() {
  const [theme, setTheme] = useState(() => {
    const initial = getInitialTheme();
    applyTheme(initial);
    return initial;
  });
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    saveTheme(next);
    applyTheme(next);
  };
  return (
    <div className="app-shell">
      <HistorySidebar />
      <QueryResultDemo theme={theme} onToggleTheme={toggleTheme} />
    </div>
  );
}

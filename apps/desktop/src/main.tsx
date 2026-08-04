import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/tokens.css";
import "./styles.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/instrument-console.css";
import "./styles/run-cockpit.css";
import "./styles/hydrated-ad4.css";
import "./styles/workspace-console.css";
import "./styles/batch-screening.css";
import "./styles/flexible-receptor.css";
import "./styles/multiple-ligand-docking.css";
import "./styles/screening-archive-comparison.css";
import "./styles/help-center.css";

const savedTheme = window.localStorage.getItem("dockstart-theme");
document.documentElement.dataset.theme = savedTheme === "light" ? "light" : "dark";
document.documentElement.style.colorScheme = savedTheme === "light" ? "light" : "dark";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

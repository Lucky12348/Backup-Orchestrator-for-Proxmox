import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
// New UI layer (Tailwind, Geist): scoped to .pbo-ui, see src/ui/ui.css.
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./ui/ui.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

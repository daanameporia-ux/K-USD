import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { boot } from "@shared/telegram";
import "@shared/theme.css";
import "./admin.css";
import { App } from "./App";

boot();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

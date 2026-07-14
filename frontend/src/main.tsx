import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted fonts (bundled by Vite — no third-party font request, per the
// project's privacy stance). Overpass/Overpass Mono: a superfamily derived
// from US highway-signage lettering — body text in the sans, labels and
// numeric readouts in the mono.
import "@fontsource/overpass/400.css";
import "@fontsource/overpass/600.css";
import "@fontsource/overpass-mono/400.css";
import "@fontsource/overpass-mono/500.css";
import "@fontsource/overpass-mono/600.css";
import "@fontsource/overpass-mono/700.css";

import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

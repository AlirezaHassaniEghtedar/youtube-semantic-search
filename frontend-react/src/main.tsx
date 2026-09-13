import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "./context/ThemeContext";
import { ToastProvider } from "./context/ToastContext";
import { TranscriptModalProvider } from "./context/TranscriptModalContext";
import { AppShell } from "./components/AppShell";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <TranscriptModalProvider>
          <AppShell />
        </TranscriptModalProvider>
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>
);

"use client";

import { createContext, useContext, useState } from "react";

interface NavState {
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
}

const NavContext = createContext<NavState | null>(null);

// Shared drawer open/close state so the ☰ button (TopNav) and the off-canvas
// sidebar (Sidebar) can talk without making the root layout a client component.
export function NavProvider({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <NavContext.Provider value={{ drawerOpen, setDrawerOpen }}>
      {children}
    </NavContext.Provider>
  );
}

export function useNav(): NavState {
  const ctx = useContext(NavContext);
  if (!ctx) throw new Error("useNav must be used within a NavProvider");
  return ctx;
}

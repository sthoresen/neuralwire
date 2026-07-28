import type { Metadata, Viewport } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import TopNav from "@/components/layout/TopNav";
import { NavProvider } from "@/components/layout/NavProvider";

export const metadata: Metadata = {
  title: {
    default: "NeuralWire",
    template: "%s · NeuralWire",
  },
  description: "AI-powered stock news intelligence",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="dark">
      <body>
        <NavProvider>
          <TopNav />
          <Sidebar />
          {/* At lg+ the fixed 272px sidebar offsets the main column; below lg the
              sidebar becomes a drawer and main spans the full width. */}
          <main className="pt-[72px] px-4 pb-20 max-w-full lg:ml-[272px] lg:pt-[92px] lg:px-8 lg:max-w-[calc(272px+1200px)]">
            <div className="max-w-[1200px]">{children}</div>
          </main>
        </NavProvider>
      </body>
    </html>
  );
}

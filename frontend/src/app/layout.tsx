import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import TopNav from "@/components/layout/TopNav";

export const metadata: Metadata = {
  title: "Pulse",
  description: "Stock news dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="dark">
      <body>
        <TopNav />
        <Sidebar />
        <main
          style={{
            marginLeft: 272,
            paddingTop: 92,
            paddingLeft: 32,
            paddingRight: 32,
            paddingBottom: 80,
            maxWidth: "calc(272px + 1200px)",
          }}
        >
          <div style={{ maxWidth: 1200 }}>{children}</div>
        </main>
      </body>
    </html>
  );
}

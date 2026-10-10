import type { Metadata } from "next";
import FrontPage from "@/components/FrontPage";

export const metadata: Metadata = {
  title: "Latest from X",
};

export default function Home() {
  return <FrontPage />;
}

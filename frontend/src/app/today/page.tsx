import { redirect } from "next/navigation";

// The front page moved to /, so old /today links land there.
export default function Today() {
  redirect("/");
}

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

// Root redirects to the last-viewed ticker (stored in a cookie) so there is no
// client-side default flash — the ticker lives entirely in the URL from here on.
export default async function Root() {
  const store = await cookies();
  const t = store.get("sn-ticker")?.value || "NVDA";
  redirect(`/${t}`);
}

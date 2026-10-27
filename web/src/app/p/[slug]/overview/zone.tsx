"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ZONE_COOKIE } from "@/lib/filters";

/** Tells the server the viewer's time zone, so days and hours are theirs. It refreshes once, when
 * the cookie changes: the server may still render in UTC if it can't use the browser's zone, and
 * comparing with what it rendered would then refresh forever. Renders nothing. */
export function ViewerZone() {
  const router = useRouter();
  useEffect(() => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const sent = document.cookie
      .split("; ")
      .find((c) => c.startsWith(`${ZONE_COOKIE}=`))
      ?.slice(ZONE_COOKIE.length + 1);
    if (!zone || (sent && decodeURIComponent(sent) === zone)) return;
    document.cookie = `${ZONE_COOKIE}=${encodeURIComponent(zone)}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  }, [router]);
  return null;
}

import { auth } from "@/lib/auth/server";

// Signed-out visitors to /chat are sent to the sign-in page.
export default auth.middleware({ loginUrl: "/auth/sign-in" });

export const config = {
  matcher: ["/chat/:path*", "/admin/:path*"],
};

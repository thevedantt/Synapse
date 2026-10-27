import { createAuthClient } from "@neondatabase/auth/next";

// No arguments: talks to our own /api/auth proxy (same origin).
export const authClient = createAuthClient();

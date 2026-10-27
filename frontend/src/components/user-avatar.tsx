/** Profile picture (e.g. Google photo), or initials in a circle when there is none. */
export function UserAvatar({ name, image }: { name: string; image?: string | null }) {
  if (image) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- tiny external Google photo
      <img
        src={image}
        alt={name}
        referrerPolicy="no-referrer"
        className="size-9 rounded-full border border-vibe-purple object-cover"
      />
    );
  }
  const initials = name
    .replace(/\(.*\)/, "")
    .trim()
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <div
      aria-label={name}
      className="flex size-9 items-center justify-center rounded-full bg-deep-purple text-sm font-medium text-white"
    >
      {initials || "?"}
    </div>
  );
}

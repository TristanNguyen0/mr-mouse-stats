import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Marvel Rivals pro mouse settings",
  description:
    "Mouse settings and settings history for professional Marvel Rivals players in the Ignite circuit.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <Link href="/">Overview</Link>
          <Link href="/players/">Players</Link>
          <Link href="/admin/">Admin</Link>
        </nav>
        {children}
        <footer>
          Player and tournament data from{" "}
          <a href="https://liquipedia.net/marvelrivals/">Liquipedia</a>, licensed{" "}
          <a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA 3.0</a>.
          Settings observed passively from public Twitch chat.
        </footer>
      </body>
    </html>
  );
}

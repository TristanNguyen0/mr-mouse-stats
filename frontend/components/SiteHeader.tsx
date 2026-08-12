"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search } from "@/components/Search";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/players/", label: "Players" },
  { href: "/admin/", label: "Admin" },
];

export function SiteHeader() {
  const pathname = usePathname();
  // trailingSlash: true, so pathnames come back with the slash the hrefs have.
  // The player detail page belongs to the Players tab.
  const active = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname.startsWith(href) || (href === "/players/" && pathname.startsWith("/player/"));

  return (
    <header className="site-header">
      <div className="site-header-inner">
        <Link href="/" className="brand">
          <span className="mark">◆</span>
          Rivals Mouse Settings
          <span className="sub">Ignite circuit</span>
        </Link>
        <nav className="tabs">
          {TABS.map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              className={active(tab.href) ? "active" : undefined}
            >
              {tab.label}
            </Link>
          ))}
        </nav>
        <div className="header-search">
          <Search />
        </div>
      </div>
    </header>
  );
}

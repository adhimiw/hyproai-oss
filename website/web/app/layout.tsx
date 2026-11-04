import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "HyproAI OSS",
    template: "%s · HyproAI OSS",
  },
  description:
    "AI video editor for Indian languages: auto-cut dead air, Thanglish captions, and export to Premiere, Resolve, and After Effects. Open source and self-hostable.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <header className="site-header">
          <div className="container header-inner">
            <Link href="/" className="wordmark">
              hypro<span className="accent">ai</span>{" "}
              <span className="suffix">oss</span>
            </Link>
            <nav className="header-nav" aria-label="Site">
              <a className="nav-link" href="#">
                GitHub
              </a>
            </nav>
          </div>
        </header>
        {children}
        <footer className="site-footer">
          <div className="container footer-inner">
            <p>HyproAI OSS — self-hosted AI video editing for Indian languages.</p>
            <p className="mono">MIT licensed · sidecar API v0.1.0</p>
          </div>
        </footer>
      </body>
    </html>
  );
}

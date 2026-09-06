import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SENTINEX — AI Agent Runtime Security",
  description:
    "Runtime security platform for AI agents. Upload, sandbox, intercept, and analyze.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
      <body>
        <nav className="navbar">
          <Link href="/" className="navbar-brand">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
            <span>SENTINEX</span>
          </Link>
          <div className="navbar-links">
            <Link href="/">Dashboard</Link>
            <a
              href="https://github.com/sentinex/sentinex"
              target="_blank"
              rel="noreferrer noopener"
            >
              Docs
            </a>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}

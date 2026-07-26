import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Sentinel — AI SRE",
  description: "Chat with your AI SRE agent about your Kubernetes cluster.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.className} flex h-screen flex-col`}>
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-gray-800 px-6">
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold tracking-tight text-emerald-400">Sentinel</span>
            <span className="rounded bg-emerald-400/10 px-2 py-0.5 text-xs font-medium text-emerald-400">
              AI SRE
            </span>
          </div>
          <div className="ml-auto text-xs text-gray-500">Phase 2</div>
        </header>

        {/* Main content fills remaining height */}
        <main className="flex-1 overflow-hidden">{children}</main>
      </body>
    </html>
  );
}

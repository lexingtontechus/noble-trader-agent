import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { AuthProvider } from "@/lib/auth-simple";
import { Providers } from "./providers"

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Noble Trader Hermes Dashboard",
  description: "Trading platform dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="dark">
      <body className={inter.className}>
        <AuthProvider>
           <Providers>
          <Navbar />
          <main className="container mx-auto px-4 py-6 max-w-7xl flex-1 min-h-screen">
            {children}
          </main>
          <Footer />
           </Providers>
        </AuthProvider>
      </body>
    </html>
  );
}

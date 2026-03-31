import { ClerkProvider } from "@clerk/nextjs";
import { IBM_Plex_Mono, Syne } from "next/font/google";
import "./globals.css";
//import "../styles/globals.css";
import AppHeader from "../components/AppHeader";
import Footer from "../components/Footer";

const syne = Syne({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-syne",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-ibm-mono",
  display: "swap",
});

export const metadata = {
  title: "Noble Trading Risk Manager",
  description: "By Noble Trading App",
};

export default function RootLayout({ children }) {
  return (
    <ClerkProvider>
      <html lang="en" className={`${syne.variable} ${ibmPlexMono.variable}`}>
        <body>
          <AppHeader />
          <div className="container mx-auto">{children}</div>
          <Footer />
        </body>
      </html>
    </ClerkProvider>
  );
}

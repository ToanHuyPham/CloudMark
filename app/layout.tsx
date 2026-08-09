import type { Metadata } from "next";
import { Manrope, IBM_Plex_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") || (host.startsWith("localhost") || host.startsWith("127.0.0.1") ? "http" : "https");
  const base = new URL(`${protocol}://${host}`);
  const title = "CloudMark — Infrastructure Assessment Platform";
  const description = "Full-stack, evidence-driven assessment for cloud, VPS, bare-metal systems and infrastructure providers.";
  return {
    metadataBase: base,
    applicationName: "CloudMark",
    title,
    description,
    icons: { icon: "/favicon.svg" },
    openGraph: {
      type: "website",
      siteName: "CloudMark",
      title,
      description,
      images: [{ url: new URL("/og-v040.png", base).toString(), width: 1672, height: 941, alt: "CloudMark evidence-driven infrastructure assessment" }],
    },
    twitter: { card: "summary_large_image", title, description, images: [new URL("/og-v040.png", base).toString()] },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${manrope.variable} ${plexMono.variable}`}>
        {children}
      </body>
    </html>
  );
}

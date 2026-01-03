import "./globals.css";

export const metadata = {
  title: "SwiftCraft Demo",
  description: "SwiftCraft DEMO 1.1 Sprint-1 mock foundation"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="noise">
        <div className="min-h-screen">{children}</div>
      </body>
    </html>
  );
}

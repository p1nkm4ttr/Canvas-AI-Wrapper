import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata = { title: "HULMS Assistant" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "'Segoe UI', system-ui, sans-serif" }}>
        {children}
      </body>
    </html>
  );
}

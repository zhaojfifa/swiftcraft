import Link from 'next/link';

export default function HomePage() {
  return (
    <main style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <div style={{
          width: 34, height: 34, borderRadius: 10,
          background: '#2563eb', display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center',
          color: 'white', fontWeight: 700
        }}>
          ✦
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>SwiftCraft</div>
      </header>

      <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 24 }}>
        <h1 style={{ fontSize: 40, margin: 0, fontWeight: 800, letterSpacing: -0.5 }}>
          Select a Service
        </h1>
        <p style={{ marginTop: 10, color: '#6b7280', fontSize: 18 }}>
          Choose a pipeline to start your generation task.
        </p>

        <div style={{ marginTop: 22, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
          <Link
            href="/workspace/swap"
            style={{
              border: '1px solid #e5e7eb',
              borderRadius: 16,
              padding: 18,
              textDecoration: 'none',
              color: 'inherit',
              display: 'block'
            }}
          >
            <div style={{ fontWeight: 800, fontSize: 18 }}>Swap</div>
            <div style={{ color: '#6b7280', marginTop: 6, lineHeight: 1.4 }}>
              Demo (mock output). Modes: baseline / intelligent.
            </div>
            <div style={{ marginTop: 12, color: '#2563eb', fontWeight: 700 }}>
              Open →
            </div>
          </Link>
        </div>
      </section>
    </main>
  );
}

import { useState } from 'react'

const BUILD_TIME = new Date().toISOString()

export default function App() {
  const [count, setCount] = useState(0)

  return (
    <main className="shell">
      <h1>Hello, world 👋</h1>
      <p className="lede">
        Vercel deployment harness for <strong>ab-verified</strong>. If you can read
        this on a *.vercel.app URL, the push-to-main pipeline works.
      </p>

      <button onClick={() => setCount((c) => c + 1)}>
        React is alive — clicked {count} {count === 1 ? 'time' : 'times'}
      </button>

      <dl className="meta">
        <dt>Mode</dt>
        <dd>{import.meta.env.MODE}</dd>
        <dt>Rendered at</dt>
        <dd>{BUILD_TIME}</dd>
      </dl>
    </main>
  )
}
